# easy-opal Architecture Report

A comprehensive technical report of the entire easy-opal codebase. This document covers every module, function, template, and script — how they work, what they do, and how they connect.

---

## Overview

easy-opal is a CLI tool for deploying and managing [OBiBa Opal](https://www.obiba.org/pages/products/opal/) environments using Docker and NGINX. It provides an interactive setup wizard, service version management, SSL certificate handling, Rock R server profile management, health diagnostics, and configuration snapshots.

**System dependencies:** Python 3.8+, Docker (with Compose V2 or V1), Git.
**Python dependencies:** click, rich, ruamel-yaml, requests, cryptography.

---

## Directory Structure

```
easy-opal/
├── src/
│   ├── cli.py                          # CLI entry point — routes all commands
│   ├── core/
│   │   ├── config_manager.py           # Configuration persistence & snapshots
│   │   ├── docker_manager.py           # Docker Compose generation & orchestration
│   │   ├── ssl_manager.py              # Self-signed certificate generation (cryptography lib)
│   │   └── nginx_manager.py            # NGINX config templating
│   ├── commands/
│   │   ├── setup_cmd.py                # Interactive/non-interactive setup wizard
│   │   ├── lifecycle_cmds.py           # up / down / reset / status
│   │   ├── config_cmds.py              # change-password, change-port, change-version, watchtower, export/import, restore
│   │   ├── cert_cmds.py                # Certificate regeneration & Let's Encrypt renewal
│   │   ├── profile_cmds.py             # Rock R server profile add/remove/list
│   │   ├── diagnostic_cmd.py           # Comprehensive health checks with retry logic
│   │   └── update_cmd.py              # Git-based tool updates & Opal version management
│   └── templates/
│       ├── docker-compose.yml.tpl      # Docker Compose base template
│       ├── nginx.conf.tpl              # HTTPS NGINX reverse proxy config
│       ├── nginx-acme.conf.tpl         # HTTP-only config for Let's Encrypt challenges
│       └── maintenance.html            # Auto-refreshing maintenance page
├── easy-opal                           # Bash wrapper (runs CLI via .venv)
├── setup                               # System dependency installer (~1850 lines bash)
└── pyproject.toml                      # Project metadata & dependencies
```

---

## Data Flow

```
User runs ./easy-opal <command>
        │
        ▼
    easy-opal (bash wrapper)
        │  activates .venv, runs: python -m src.cli
        ▼
    src/cli.py (Click command group)
        │  routes to the appropriate command module
        ▼
    src/commands/<command>.py
        │  uses core modules for business logic
        ▼
    src/core/
    ├── config_manager.py   → reads/writes config.json, creates snapshots
    ├── docker_manager.py   → generates docker-compose.yml, runs docker compose
    ├── ssl_manager.py      → generates CA + server certificates
    └── nginx_manager.py    → generates nginx.conf from templates
```

---

## Persistence Model

| File | Purpose | Managed by |
|------|---------|------------|
| `config.json` | Single source of truth for all configuration | config_manager.py |
| `docker-compose.yml` | Generated artifact — never edited manually | docker_manager.py |
| `data/nginx/conf/nginx.conf` | Generated artifact | nginx_manager.py |
| `data/nginx/certs/` | SSL certificates (ca.crt, ca.key, opal.crt, opal.key) | ssl_manager.py |
| `.env` | Admin password only (`OPAL_ADMIN_PASSWORD=...`) | setup_cmd.py, config_cmds.py |
| `backups/{YYYYMMDD_HHMMSS}/` | Timestamped snapshots of config.json + docker-compose.yml | config_manager.py |

---

## Core Modules

### config_manager.py

Central configuration hub. Every other module depends on it.

**Default config structure:**
```json
{
  "stack_name": "easy-opal",
  "hosts": ["localhost", "127.0.0.1"],
  "opal_version": "latest",
  "mongo_version": "latest",
  "nginx_version": "latest",
  "opal_external_port": 443,
  "opal_http_port": 8080,
  "profiles": [{ "name": "rock", "image": "datashield/rock-base", "tag": "latest" }],
  "databases": [],
  "watchtower": { "enabled": false, "poll_interval": 86400, "cleanup": true },
  "ssl": { "strategy": "self-signed", "cert_path": "...", "key_path": "...", "le_email": "" }
}
```

**Key functions:**
- `get_default_config()` → returns the default config dict
- `load_config()` → reads config.json (creates default if missing)
- `save_config(config)` → writes config.json
- `create_snapshot(reason)` → copies config.json + docker-compose.yml to `backups/{timestamp}/`
- `ensure_password_is_set()` → checks .env exists, prompts if not
- `ensure_directories_exist()` → creates data/, certs/, conf/ directories

### docker_manager.py

Generates docker-compose.yml from the template + config, and runs all Docker Compose commands.

**`generate_compose_file()` logic:**
1. Loads `src/templates/docker-compose.yml.tpl` with ruamel.yaml
2. Sets image versions for Opal, MongoDB, NGINX from config
3. Adds database services (PostgreSQL/MySQL/MariaDB) with env vars for Opal connectivity
4. Applies SSL strategy:
   - `none`: removes NGINX and Certbot, exposes Opal directly on HTTP
   - `self-signed` / `manual`: configures NGINX HTTPS ports
   - `letsencrypt`: keeps Certbot, opens port 80 for ACME challenges
5. Adds Rock server profiles as services
6. Adds Watchtower service if enabled
7. Writes final docker-compose.yml

**Docker Compose compatibility:**
- Tries `docker compose` (V2) first, falls back to `docker-compose` (V1)
- Both syntaxes handled transparently via `get_docker_compose_command()`

**Other functions:**
- `run_docker_compose(command, project_name)` → executes compose commands with real-time output
- `docker_up()`, `docker_down()`, `docker_restart()`, `docker_reset()`, `docker_status()` → lifecycle helpers
- `pull_docker_image(name)` → pulls with real-time progress streaming
- `check_docker_installed()` → validates Docker engine, daemon, Compose, and version

### ssl_manager.py

Generates self-signed SSL certificates using Python's `cryptography` library. No external tools needed.

**How it works:**
1. `_generate_ca()` creates a local Certificate Authority:
   - RSA 2048-bit key
   - Self-signed X509 cert (CN=easy-opal Local CA), valid 10 years
   - BasicConstraints: ca=True
2. `generate_self_signed_cert(cert_path, key_path)`:
   - Generates CA (saved to `ca.crt` / `ca.key`)
   - Generates server key (RSA 2048-bit)
   - Creates server cert signed by the CA, valid 825 days
   - SubjectAlternativeName includes all configured hosts (DNS names + IP addresses)
   - Saves to the specified cert_path and key_path

**Browser trust:** The CA cert is not auto-installed in the system trust store. Users can optionally import `data/nginx/certs/ca.crt` to avoid browser warnings.

### nginx_manager.py

Generates `data/nginx/conf/nginx.conf` from templates.

**Three modes:**
- `strategy == "none"` → skips NGINX entirely, cleans up old config
- `acme_only=True` → HTTP-only config for Let's Encrypt HTTP-01 challenge (temporary, during setup)
- Default → full HTTPS config with SSL termination, proxy to Opal, maintenance page

**Template substitutions:**
- `${OPAL_HOSTNAME}` → space-separated list of configured hosts
- Certificate paths adjusted for self-signed/manual vs Let's Encrypt

---

## Command Modules

### setup_cmd.py (~700 lines)

The main setup wizard. Supports both interactive and non-interactive (CLI flags) modes.

**Interactive flow:**
1. Display ASCII art header
2. Detect and handle existing configuration (stop old stack, run reset wizard)
3. General config: stack name
4. Service versions: Opal, MongoDB (optional: NGINX)
5. SSL strategy selection:
   - `none` → ask for HTTP port
   - `self-signed` → detect local IPs, let user add hosts
   - `manual` → ask for cert/key file paths
   - `letsencrypt` → ask for email and domain
6. Database configuration: add PostgreSQL/MySQL/MariaDB instances interactively
7. Watchtower: enable/disable, poll interval (hours), cleanup
8. Password: create or update .env
9. Generate configs, certs, docker-compose.yml
10. Offer to start the stack

**Non-interactive mode:** activated when all required flags are provided + `--yes`. All the same options available as CLI flags (e.g., `--stack-name`, `--ssl-strategy`, `--database type:name:port:user:pass[:version]`, `--watchtower`, `--watchtower-interval`).

**Helper functions:**
- `is_port_in_use(port)` → hybrid check: tries TCP connect then socket bind
- `find_free_port(start, reserved)` → scans for next available port
- `get_local_ip()` → detects machine's LAN IP
- `parse_database_spec(spec)` → parses `type:name:port:user:password[:version]` format

### lifecycle_cmds.py

Simple Docker lifecycle wrappers.

- `up()` → restarts stack (down + up) to apply any config changes
- `down()` → stops containers
- `status()` → shows container list
- `reset()` → selective teardown wizard (containers, volumes, configs, certs, secrets) with interactive confirmation

### config_cmds.py (~460 lines)

Post-setup configuration management.

**Subcommands:**
- `change-password [PASSWORD]` → updates .env
- `change-port [PORT]` → updates config + regenerates docker-compose.yml
- `change-version [VERSION] --service <name>` → works for opal/mongo/nginx OR any database instance by name. Optional `--pull` to download image immediately
- `show-version --service <name|all>` → table display of all service versions
- `watchtower [enable|disable|status] --interval N --cleanup/--no-cleanup` → full Watchtower lifecycle management
- `show` → pretty-prints config.json
- `export` → compresses config with zlib + base64 for sharing
- `import [STRING]` → restores config from export string with diff preview
- `restore [SNAPSHOT_ID]` → lists snapshots, shows diff, restores with confirmation

### cert_cmds.py

- `regenerate` → strategy-aware: calls `generate_self_signed_cert()` for self-signed, runs certbot for Let's Encrypt, shows message for manual
- `run_certbot()` → runs certbot renew container + reloads NGINX

### profile_cmds.py

Rock R server profile management.

- `add` → interactive or via flags (`--repository`, `--image`, `--tag`, `--name`). Pulls Docker image to validate. Adds to config and regenerates docker-compose.yml
- `remove [NAME]` → interactive selection or by name. Restarts stack with `--remove-orphans`
- `list` → displays table of all profiles with image and tag

**Profile data structure in config:**
```json
{ "name": "rock-custom", "image": "datashield/rock-custom", "tag": "latest" }
```

### diagnostic_cmd.py (~1700 lines)

Comprehensive health check system.

**Test categories:**
1. **Infrastructure** → Docker Compose file exists, container status
2. **Network Connectivity** → TCP connections between containers (Opal↔MongoDB, NGINX↔Opal, Opal↔Rock) with 2-minute retry
3. **External Access** → port accessibility from host
4. **SSL Certificates** → existence and expiration validation
5. **Service Health** → HTTP/HTTPS endpoint responses

**Retry mechanism:** Failed tests are retried every 10 seconds for up to 2 minutes. This handles containers that are still starting.

**Output modes:**
- Default: full report with troubleshooting guidance
- `--quiet`: summary line (pass/fail counts)
- `--verbose`: extra debugging info
- `--no-auto-start`: prevents interactive stack start prompt

**Smart stack detection:** If no containers are running, offers to start the stack before running diagnostics.

### update_cmd.py

- `update` → fetches from git remote, detects if behind, offers force-reset to `origin/main`, then runs `uv sync` for dependency updates
- `update --opal` → pulls the configured Opal Docker image
- `update --opal-version 5.1` → changes version in config + pulls new image

---

## Templates

### docker-compose.yml.tpl

Base template with static services (mongo, opal, nginx, certbot). All image tags, ports, environment variables, and additional services are injected dynamically by `docker_manager.py`.

### nginx.conf.tpl

Full HTTPS reverse proxy configuration:
- TLS 1.2/1.3, strong ciphers
- HTTP→HTTPS redirect
- ACME challenge passthrough (for Let's Encrypt renewal)
- Proxy to `opal:8080` with proper headers
- Maintenance page on 502/503/504

### nginx-acme.conf.tpl

Temporary HTTP-only config used during initial Let's Encrypt certificate acquisition. Serves ACME challenges, returns 503 for everything else.

### maintenance.html

Responsive Material Design page with auto-refresh. Shown by NGINX when Opal is temporarily unavailable (restarts, updates).

---

## Setup Script (`./setup`)

~1850 lines of bash for cross-platform dependency installation.

**What it does:**
1. Parses flags (`--upgrade-python`, `--upgrade-docker`)
2. Detects OS and package manager (supports 15+ distributions)
3. Installs/upgrades Python 3.8+ if needed (via PPA, EPEL, dnf, pacman, apk, source compilation)
4. Installs/upgrades Docker CE with Compose V2 (official repos per distro)
5. Installs uv (Python package manager)
6. Creates virtual environment and installs project dependencies
7. Verifies installation

**No longer installs mkcert** — SSL certificates are now generated by Python's `cryptography` library.

---

## Module Dependency Graph

```
config_manager.py          ← no internal dependencies (foundation)
    ▲
    ├── ssl_manager.py     ← reads hosts from config, writes certs
    ├── nginx_manager.py   ← reads config for strategy/hosts
    ├── docker_manager.py  ← reads full config, generates compose
    │
    └── All command modules
         ├── setup_cmd.py       → uses ALL core modules
         ├── lifecycle_cmds.py  → docker_manager
         ├── config_cmds.py     → config_manager, docker_manager, nginx_manager
         ├── cert_cmds.py       → ssl_manager, docker_manager
         ├── profile_cmds.py    → config_manager, docker_manager
         ├── diagnostic_cmd.py  → config_manager, docker_manager, subprocess
         └── update_cmd.py      → config_manager, docker_manager, subprocess (git)
```

---

## SSL Strategies

| Strategy | NGINX | Certbot | Certificates | Use case |
|----------|-------|---------|-------------|----------|
| `self-signed` | Yes | No | Generated by Python cryptography (CA + server cert) | Local development |
| `letsencrypt` | Yes | Yes | Acquired via ACME HTTP-01 challenge | Production with public domain |
| `manual` | Yes | No | User-provided .crt and .key files | Corporate/custom CA |
| `none` | No | No | None — Opal exposed on HTTP directly | Behind external reverse proxy |

---

## Database Support

Additional databases (PostgreSQL, MySQL, MariaDB) can be added alongside the default MongoDB:

- **Config format:** `{ "type": "postgres", "name": "analytics", "port": 5432, "version": "latest", "user": "opal", "password": "...", "database": "opaldata" }`
- **CLI format:** `--database postgres:analytics:5432:opal:secret[:16]`
- **Docker Compose:** each instance becomes a service with its own volume
- **Opal integration:** environment variables (`{NAME}_HOST`, `{NAME}_PORT`, `{NAME}_DATABASE`, `{NAME}_USER`, `{NAME}_PASSWORD`) are set on the Opal container

---

## Watchtower (Automatic Updates)

Optional service that monitors Docker images and auto-updates containers.

- **Config:** `{ "enabled": true, "poll_interval": 86400, "cleanup": true }`
- **User-facing interval:** hours (converted to seconds internally for Watchtower)
- **Docker Compose:** adds `containrrr/watchtower` service with Docker socket mount
- **Management:** `./easy-opal config watchtower [enable|disable|status] --interval N`
