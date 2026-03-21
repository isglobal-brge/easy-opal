# Architecture

Technical reference for the easy-opal codebase.

## Directory Structure

```
easy-opal/                     # Entry point + Python project root
  easy-opal                    # Bash bootstrap (installs uv, delegates to Python)
  pyproject.toml               # Dependencies: click, rich, pydantic, cryptography, pyyaml, requests
  .python-version              # 3.11 (managed by uv)
  src/
    cli.py                     # Click group, global -i/--instance option
    models/
      config.py                # OpalConfig + nested Pydantic models
      instance.py              # InstanceContext dataclass (paths for one deployment)
      enums.py                 # SSLStrategy, DatabaseType
    core/
      config_manager.py        # load_config / save_config (Pydantic-based)
      secrets_manager.py       # secrets.env: generate, load, save, ensure
      instance_manager.py      # Multi-instance CRUD + registry + lock + validation
      docker.py                # Docker Compose: generate, run, up, down, health-wait
      ssl.py                   # CA generation, server certs, persistent CA
      nginx.py                 # NGINX config from templates
      migration.py             # Schema version migrations (v0 → v1 → v2)
    services/
      __init__.py              # ServiceModule protocol + ServiceRegistry
      mongo.py                 # MongoDB: compose + healthcheck
      opal.py                  # Opal: compose + env var aggregation + CSRF
      nginx.py                 # NGINX: compose + SSL mounts
      certbot.py               # Certbot (Let's Encrypt only)
      rock.py                  # Rock profiles: compose + healthcheck
      database.py              # PostgreSQL/MySQL/MariaDB: compose + healthcheck
      watchtower.py            # Watchtower auto-updates
    commands/
      setup.py                 # Interactive/non-interactive setup wizard
      lifecycle.py             # up, down, restart, status, reset
      config.py                # change-version, change-port, change-hosts, change-ssl, change-password, watchtower, remove-database
      certs.py                 # regenerate, info, ca-regenerate
      profiles.py              # add, remove, list
      instances.py             # create, list, remove, info
      backup.py                # create, restore, list
      volumes.py               # list, prune
      diagnose.py              # Stack health checks
      doctor.py                # easy-opal self-diagnostics
      update.py                # Git-based self-update
    templates/
      nginx_https.conf.tpl     # HTTPS reverse proxy
      nginx_acme.conf.tpl      # HTTP-only for Let's Encrypt
      maintenance.html         # Auto-refresh maintenance page
    utils/
      console.py               # Rich console + helpers
      network.py               # Port checking, IP detection
      crypto.py                # Password generation
  tests/
    test_models.py             # Pydantic model tests (7)
    test_services.py           # Service registry tests (11)
    test_selenium_login.py     # E2E: page load, auth, CSRF, security (13)
```

## Data Flow

```
./easy-opal <command>
  │
  ▼
easy-opal (bash) → uv run → python -m src.cli
  │
  ▼
cli.py → resolves instance → routes to command
  │
  ▼
command → uses core modules:
  ├── config_manager.py   (load/save OpalConfig)
  ├── secrets_manager.py  (load/save secrets.env)
  ├── docker.py           (ServiceRegistry → compose → docker compose up)
  ├── ssl.py              (CA + server certs)
  └── nginx.py            (nginx.conf from template)
```

## Instance Layout

```
~/.easy-opal/
  registry.json              # Global index: name → path, created_at, last_accessed, stack_name
  instances/
    <name>/
      config.json            # Source of truth (Pydantic OpalConfig, schema_version: 2)
      secrets.env            # KEY=VALUE, 0o600 permissions
      docker-compose.yml     # Generated — never edit
      .lock                  # File lock (PID, auto-cleans after 10 min)
      data/
        certs/{ca,opal}.{crt,key}
        nginx/nginx.conf
        html/maintenance.html
        letsencrypt/{www,conf}/
      backups/*.tar.gz
```

## Service Registry

Each service is a module in `src/services/` that implements:

```python
class ServiceModule(Protocol):
    name: str
    def is_enabled(config) → bool
    def compose_services(config, ctx, secrets) → dict    # Docker Compose fragment
    def compose_volumes(config) → dict                   # Named volumes
    def opal_env_vars(config, secrets) → dict            # Env vars for the Opal container
```

`ServiceRegistry` collects all enabled modules, merges their compose fragments, and aggregates Opal environment variables. Adding a new service = one new file.

## Config Model

```python
OpalConfig(
    schema_version = 2,
    stack_name = "easy-opal",
    hosts = ["localhost", "127.0.0.1"],
    opal_version = "latest",
    mongo_version = "latest",
    nginx_version = "latest",
    opal_external_port = 443,
    opal_http_port = 8080,
    ssl = SSLConfig(strategy="self-signed", le_email=""),
    profiles = [ProfileConfig(name="rock", image="datashield/rock-base", tag="latest")],
    databases = [],
    watchtower = WatchtowerConfig(enabled=False, poll_interval_hours=24, cleanup=True),
)
```

Passwords are NOT in config. They're in `secrets.env`, auto-generated with `secrets.token_urlsafe(24)`.

## Config Changes → Regeneration Chain

| Change | Regenerates |
|--------|-------------|
| Hosts | Certs + CSRF + NGINX + Compose |
| Port | CSRF + NGINX + Compose |
| SSL strategy | Certs + NGINX + Compose |
| Version | Compose |
| Password | Compose (env var) |
| Watchtower | Compose |
| Database add/remove | Compose |

## Volume Naming

All named volumes use `{stack_name}-{service}-data` to prevent collisions between instances:

| Service | Volume |
|---------|--------|
| MongoDB | `{stack}-mongo-data` |
| Opal | `{stack}-opal-data` |
| Rock | `{stack}-{profile}-data` |
| Database | `{stack}-{db_name}-data` |

## Schema Migration

`migration.py` handles upgrades from older config formats:

- **v0 → v1**: Adds `schema_version`, removes `opal_admin_password`, removes `mongodb` key
- **v1 → v2**: Removes `cert_path`/`key_path` from SSL (now computed), converts `poll_interval` seconds to `poll_interval_hours`, removes `certbot_version`

Migration runs automatically on `load_config()`.

## Healthcheck Chain

```
mongo (mongosh ping) → opal (TCP 8080) → nginx (TCP 80) + rock (TCP 8085)
```

All use `depends_on: {service: {condition: service_healthy}}`. Docker Compose `--wait` ensures readiness before the CLI returns.
