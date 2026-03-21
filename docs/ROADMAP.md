# Roadmap

What's done, what's next, and what's planned long-term.

---

## Done (v2 — current)

- Hermetic bootstrap (uv, no system dependencies beyond Docker)
- Multi-instance management with registry
- Pydantic config with schema migrations
- Service registry (modular compose generation)
- All services: MongoDB, Opal, NGINX, Certbot, Rock, PostgreSQL/MySQL/MariaDB, Watchtower
- SSL: self-signed (Python cryptography, persistent CA), Let's Encrypt, manual, none
- Security: random passwords, secrets.env, CSRF from hosts, file permissions, PEM validation
- Backup/restore with native DB dumps
- Health diagnostics (stack + self)
- Atomic file locking
- 54 tests (models, services, migration, core, e2e)

---

## Phase 2: UX and Robustness

Focus: polish the daily experience, cover edge cases, make errors impossible.

### 2.1 Dry-run mode

Add `--dry-run` to setup and config commands. Shows what would change without applying.

```
easy-opal setup --dry-run
easy-opal config change-ssl letsencrypt --dry-run
```

**Implementation:**
- Add `--dry-run` flag to setup, lifecycle, and config commands
- In dry-run: generate compose to stdout instead of writing, skip Docker calls
- Show diff between current and proposed config

**Files:** `src/commands/setup.py`, `src/commands/config.py`, `src/commands/lifecycle.py`

### 2.2 Config diff on changes

Before applying config changes, show what will change.

```
easy-opal config change-hosts opal.dev
  hosts: ["localhost", "127.0.0.1"] -> ["opal.dev"]
  CSRF: https://localhost:443 -> https://opal.dev:443
  Certificates: will be regenerated
  Apply? [y/n]
```

**Implementation:**
- Compare old and new OpalConfig, show differences
- Add `--review` flag (default on for interactive, off for `--yes`)

**Files:** `src/commands/config.py`, new `src/utils/diff.py`

### 2.3 Structured logging

Replace Rich console printing with Python `logging` module for machine-readable output.

```
easy-opal up --log-format json    # For CI/CD pipelines
easy-opal up                      # Default: Rich formatted output
```

**Implementation:**
- Keep `src/utils/console.py` for interactive output
- Add `src/utils/logging.py` with JSON handler
- Global `--log-format` option in CLI

**Files:** new `src/utils/logging.py`, `src/cli.py`

### 2.4 Support bundle

One command that collects everything needed for debugging.

```
easy-opal support-bundle -o bundle.zip
```

Contents: redacted config, compose, Docker ps, container logs (last 100 lines), doctor output, cert info, system info.

**Implementation:**
- Collect data from existing commands (doctor, diagnose, cert info)
- Redact passwords from all output
- Package as zip

**Files:** new `src/commands/support.py`

### 2.5 Database connection testing

Verify databases are reachable from Opal after setup.

```
easy-opal diagnose --databases
```

**Implementation:**
- For each database, exec into the Opal container and test TCP connectivity
- Report pass/fail per database

**Files:** `src/commands/diagnose.py`

### 2.6 Windows support (WSL2)

Document and test running easy-opal under Windows Subsystem for Linux.

- Add `easy-opal.ps1` PowerShell wrapper that invokes WSL
- Test bootstrap under WSL2 Ubuntu
- Document in README

**Files:** new `easy-opal.ps1`, `README.md`

---

## Phase 3: Agate

Focus: add OBiBa Agate as a companion service for authentication and notifications.

### Why Agate

Opal supports delegating authentication to Agate. Agate provides:
- User management (registration, password reset)
- Group/permission management
- Email notifications (SMTP, OAuth2)
- Single sign-on across Opal/Mica

### 3.1 Agate service module

```python
# src/services/agate.py
class AgateService:
    name = "agate"
    # image: obiba/agate:latest
    # depends_on: mongo (service_healthy)
    # ports: 8444:8443 (or behind shared NGINX)
    # env: AGATE_ADMINISTRATOR_PASSWORD, MONGO_HOST, MONGO_PORT
    # healthcheck: TCP 8443
```

**Config model addition:**
```python
class AgateConfig(BaseModel):
    enabled: bool = False
    version: str = "latest"
    port: int = 8444
```

**Integration with Opal:**
- Set `AGATE_URL=https://agate:8444` on Opal container when Agate enabled
- Shared MongoDB or dedicated database (configurable)

### 3.2 Mail service for development

When Agate is enabled, offer Mailpit for local email testing.

```python
# src/services/mailpit.py
class MailpitService:
    name = "mailpit"
    # image: axllent/mailpit:latest
    # ports: 8025:8025 (web UI), 1025:1025 (SMTP)
    # Only enabled when agate.enabled and mail_mode == "mailpit"
```

### 3.3 NGINX multi-service routing

Update NGINX config to route to both Opal and Agate based on path or subdomain.

```
https://opal.example.com -> opal:8080
https://agate.example.com -> agate:8443
```

Or path-based:
```
/opal/* -> opal:8080
/agate/* -> agate:8443
```

### 3.4 Setup wizard for Agate

```
4. Agate (Authentication Server)
   Enable Agate? [y/n] (n): y
   Agate version (latest):
   Email mode [mailpit/smtp/none] (mailpit):
```

**CLI:**
```
easy-opal setup --with-agate --mail-mode mailpit
easy-opal config agate enable
easy-opal config agate disable
```

### 3.5 Post-init hooks

After Agate starts, configure Opal to use it as identity provider.

**Files to create:**
- `src/services/agate.py`
- `src/services/mailpit.py`
- `src/models/config.py` (add AgateConfig)
- Update `src/services/opal.py` (AGATE_URL env var)
- Update `src/core/nginx.py` (multi-service routing)
- Update `src/commands/setup.py` (Agate wizard step)
- Update `src/commands/config.py` (agate enable/disable)

---

## Phase 4: Mica

Focus: add OBiBa Mica for data portal and metadata cataloging.

### Why Mica

Mica is the data portal layer. It depends on:
- Opal (data source)
- Agate (authentication) — required, not optional
- Elasticsearch 8.x (search index)

### 4.1 Prerequisites

Agate must be implemented first (Phase 3). Mica requires it.

### 4.2 Service modules

```python
# src/services/mica.py
class MicaService:
    name = "mica"
    # image: obiba/mica:latest
    # depends_on: mongo, elasticsearch, agate (all service_healthy)
    # ports: 8445:8443
    # env: MICA_ADMINISTRATOR_PASSWORD, MONGO_*, AGATE_URL, ES_URL

# src/services/elasticsearch.py
class ElasticsearchService:
    name = "elasticsearch"
    # image: docker.elastic.co/elasticsearch/elasticsearch:8.16.1
    # env: discovery.type=single-node, xpack.security.enabled=false
    # healthcheck: curl localhost:9200/_cluster/health
    # memory: requires ES_JAVA_OPTS=-Xms512m -Xmx512m minimum
```

### 4.3 Resource checking

Before deploying Mica stack, check system resources:
- Minimum 4GB RAM available for containers
- Minimum 10GB disk space
- Warn if insufficient

```
easy-opal setup --with-mica
  Warning: Mica + Elasticsearch requires at least 4GB RAM.
  Current available: 2.1GB. Continue? [y/n]
```

### 4.4 Version matrix

Mica has strict version dependencies. Pin versions in a compatibility matrix:

```python
MICA_COMPAT = {
    "mica:latest": {"elasticsearch": "8.16.1", "agate": "latest", "mongo": "<=8.0"},
    "mica:7.0": {"elasticsearch": "8.14.0", "agate": "2.8", "mongo": "<=7.0"},
}
```

**Files to create:**
- `src/services/mica.py`
- `src/services/elasticsearch.py`
- `src/models/config.py` (add MicaConfig, ElasticsearchConfig)
- new `src/core/compat.py` (version compatibility matrix)

---

## Phase 5: Distribution

Focus: make easy-opal installable without cloning the repo.

### 5.1 PyPI / uv tool install

Publish to PyPI so users can:

```bash
uv tool install easy-opal           # Install globally
easy-opal setup                     # Works from anywhere
easy-opal update                    # Self-update
```

**Requirements:**
- Publish package to PyPI (or GitHub Packages)
- `update` command pulls new version via `uv tool upgrade`
- Templates bundled inside the package via `importlib.resources`

### 5.2 Offline bundle

For air-gapped environments:

```bash
easy-opal bundle create -o opal-bundle.tar.gz
# Contains: uv binary, Python, wheels, Docker images (docker save)

# On target machine:
easy-opal bundle install opal-bundle.tar.gz --offline
```

### 5.3 Single-binary distribution

Use PyInstaller or Nuitka to create a standalone binary:

```bash
# Download and run — no Python needed
curl -L https://github.com/.../releases/download/v2.0/easy-opal-linux-x64 -o easy-opal
chmod +x easy-opal
./easy-opal setup
```

---

## Phase 6: Armadillo

Focus: support DataSHIELD Armadillo as an alternative/companion to Opal.

### Why separate

Armadillo is not part of OBiBa. It's a lighter DataSHIELD server. It should be a flavor/plugin, not mixed into the Opal workflow.

### 6.1 Flavor system

```yaml
# easy-opal.yaml or via CLI
flavor: armadillo    # instead of default "opal"
```

Different flavors get different service sets:
- `opal` (default): MongoDB + Opal + NGINX + Rock
- `opal+agate`: + Agate + Mailpit
- `obiba-full`: + Agate + Mica + Elasticsearch
- `armadillo`: Armadillo + NGINX (no Opal)

### 6.2 Armadillo service module

```python
# src/services/armadillo.py
class ArmadilloService:
    name = "armadillo"
    # image: molgenis/molgenis-armadillo:latest
    # ports: 8080
    # profile management via docker.sock or compose profiles
    # auth: local or OIDC
```

---

## Phase 7: Advanced Operations

### 7.1 Presets

Named configuration templates:

```bash
easy-opal setup --preset opal-dev        # Self-signed, localhost, Watchtower
easy-opal setup --preset opal-prod       # Let's Encrypt, hardened
easy-opal setup --preset obiba-full      # Opal + Agate + Mica + ES
```

Presets are YAML files in `src/presets/` that provide default values.

### 7.2 Config overlay system

```
defaults → preset → config file → env vars → CLI flags
```

Each layer overrides the previous. Allows maximum flexibility without complexity.

### 7.3 Plan/render commands

```bash
easy-opal plan          # Show what docker-compose.yml would look like
easy-opal render        # Write docker-compose.yml without starting
easy-opal validate      # Check config + compose are valid
```

### 7.4 Podman support

Detect Podman as alternative to Docker:

```python
runtime = detect_runtime()  # "docker" or "podman"
```

Adapt compose commands accordingly (`podman compose` vs `docker compose`).

### 7.5 External services

Support connecting to services running outside the stack:

```yaml
databases:
  - type: postgres
    name: external-db
    mode: external          # Don't create container
    host: db.example.com    # Connect to this host
    port: 5432
```

---

## Priority Summary

| Phase | Focus | Depends on | Effort |
|-------|-------|-----------|--------|
| 2 | UX polish | Nothing | Small |
| 3 | Agate | Phase 2 (optional) | Medium |
| 4 | Mica | Phase 3 (required) | Medium |
| 5 | Distribution | Nothing | Small |
| 6 | Armadillo | Phase 2 | Medium |
| 7 | Advanced ops | Phase 3-4 | Large |

**Recommended order:** Phase 2 and 5 can happen in parallel. Then Phase 3. Then Phase 4. Phase 6 and 7 are independent and can happen anytime after Phase 2.
