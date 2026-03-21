# Architecture

Technical reference for the easy-opal codebase.

## File Structure

```
src/
  cli.py                     # Click group, global -i/--instance, command routing
  __main__.py                # python -m src entry point
  models/
    config.py                # Pydantic: OpalConfig, SSLConfig, DatabaseConfig, ProfileConfig,
                             #   WatchtowerConfig, AgateConfig, SmtpConfig, MicaConfig
    instance.py              # InstanceContext dataclass (computed paths for one deployment)
    enums.py                 # SSLStrategy, DatabaseType
  core/
    config_manager.py        # load_config / save_config (Pydantic + migration)
    secrets_manager.py       # secrets.env: generate, load, save, ensure
    instance_manager.py      # Multi-instance CRUD, registry, lock, name validation
    docker.py                # Docker/Podman detection, compose generate/run/up/down
    ssl.py                   # Persistent CA, server certs, file permissions
    nginx.py                 # Programmatic NGINX config (multi-service routing)
    migration.py             # Schema version migrations (v0 -> v1 -> v2)
    agate_config.py          # Generate Agate application-prod.yml for email
  services/
    __init__.py              # ServiceModule protocol + ServiceRegistry
    mongo.py                 # MongoDB
    opal.py                  # Opal (env var aggregation, CSRF)
    nginx.py                 # NGINX (SSL, multi-service routing)
    certbot.py               # Certbot (Let's Encrypt only)
    rock.py                  # Rock profiles (one per profile)
    database.py              # PostgreSQL / MySQL / MariaDB (local or external)
    watchtower.py            # Watchtower auto-updates
    agate.py                 # Agate authentication (opt-in)
    mailpit.py               # Mailpit dev mail (opt-in, with Agate)
    mica.py                  # Mica data portal (opt-in)
    elasticsearch.py         # Elasticsearch (opt-in, with Mica)
  presets/
    __init__.py              # Named config templates (opal-dev, opal-prod, etc.)
  commands/
    setup.py                 # Interactive/non-interactive setup wizard
    lifecycle.py             # up, down, restart, status, reset, plan, validate
    config.py                # change-*, show-*, watchtower, agate, mica, remove-database
    certs.py                 # regenerate, info, ca-regenerate
    profiles.py              # add, remove, list
    instances.py             # create, list, info, remove
    backup.py                # create, restore, list
    volumes.py               # list, prune
    diagnose.py              # Stack health checks (containers, SSL, endpoints, databases)
    doctor.py                # Self-diagnostics (Docker, config, secrets, permissions)
    support.py               # Support bundle (redacted diagnostics zip)
    update.py                # Smart update (git or uv tool)
  templates/
    maintenance.html         # Auto-refresh maintenance page
  utils/
    console.py               # Rich console + helpers
    network.py               # Port check, free port, local IP, port validation
    crypto.py                # Password generation
    diff.py                  # Config diff, compose preview
tests/
  test_models.py             # Pydantic model tests
  test_services.py           # Service registry tests
  test_migration.py          # Schema migration tests
  test_core.py               # Config, secrets, SSL, network, crypto tests
  test_selenium_login.py     # E2E: page load, auth, CSRF, security
install.sh                   # One-liner installer
pyproject.toml               # Dependencies: click, rich, pydantic, pyyaml, cryptography, requests
.python-version              # 3.11 (managed by uv)
```

## Instance Layout

```
~/.easy-opal/
  registry.json              # name -> path, created_at, last_accessed, stack_name
  instances/
    <name>/
      config.json            # Source of truth (Pydantic OpalConfig, schema_version: 2)
      secrets.env            # KEY=VALUE, 0o600 permissions
      docker-compose.yml     # Generated from config (never edit manually)
      .lock                  # File lock (PID, fcntl)
      data/
        certs/{ca,opal}.{crt,key}
        nginx/nginx.conf
        html/maintenance.html
        letsencrypt/{www,conf}/
        agate/conf/application-prod.yml
      backups/*.tar.gz
```

## Service Registry

Each service is a module in `src/services/` implementing:

```python
class ServiceModule(Protocol):
    name: str
    def is_enabled(config) -> bool
    def compose_services(config, ctx, secrets) -> dict
    def compose_volumes(config) -> dict
    def opal_env_vars(config, secrets) -> dict
```

`ServiceRegistry` collects all enabled modules, merges their compose fragments, and aggregates Opal environment variables. Adding a new service = one file.

Services: mongo, opal, nginx, certbot, rock (per profile), database (per db), watchtower, agate, mailpit, mica, elasticsearch.

## Config Changes -> Regeneration

| Change | Regenerates |
|--------|-------------|
| Hosts | Certs + CSRF + NGINX + Compose |
| Port | CSRF + NGINX + Compose |
| SSL strategy | Certs + NGINX + Compose |
| Version | Compose |
| Password | Compose |
| Watchtower | Compose |
| Database add/remove | Compose |
| Agate enable/disable | Agate config + NGINX + Compose |
| Agate mail mode | Agate config + Compose |
| Mica enable/disable | Compose |

## Volume Naming

All named volumes: `{stack_name}-{service}-data`. No collisions between instances.

## Container Runtime

Auto-detects Docker or Podman. Uses `docker compose` or `podman compose`.

## Schema Migration

Runs on `load_config()`: v0 -> v1 -> v2. Persists migrated config automatically.

## Healthcheck Chain

```
mongo (mongosh ping)
  -> opal (TCP 8080, start_period: 60s)
    -> nginx (service status)
    -> rock (TCP 8085, start_period: 30s)
    -> agate (TCP 8444, start_period: 30s)
    -> mica (TCP 8445, start_period: 60s)
```

All use `depends_on: {service: {condition: service_healthy}}`.

## NGINX Multi-Service Routing

Generated programmatically from config. One location block per enabled service:

- `/` -> opal:8080
- `/agate/` -> agate:8444 (if enabled)
- `/mica/` -> mica:8445 (if enabled)

Each location has independent `error_page 502 503 504` pointing to the maintenance page with path-aware auto-refresh.

## Security

- Passwords: `secrets.token_urlsafe(24)`, stored in `secrets.env` (0o600)
- SSL keys: 0o600 permissions
- CSRF: computed from hosts + port, not `*`
- Persistent CA: regenerating server cert preserves browser trust
- PEM validation on manual cert import
- Atomic file locking (fcntl)
- Let's Encrypt rollback to self-signed on failure
