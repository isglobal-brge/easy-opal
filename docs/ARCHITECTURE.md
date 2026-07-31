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
    container_runtime.py     # Runtime selection, binding, probing, command execution
    docker.py                # Compatibility wrappers + Compose generate/run/up/down
    auto_update.py           # Image pull, health verification, and compensating rollback
    auto_update_scheduler.py # systemd/launchd maintenance schedule management
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
    agate.py                 # Agate authentication (opt-in)
    mailpit.py               # Mailpit dev mail (opt-in, with Agate)
    mica.py                  # Mica data portal (opt-in)
    elasticsearch.py         # Elasticsearch (opt-in, with Mica)
    armadillo.py             # Armadillo DataSHIELD server (flavor)
    armadillo_rock.py        # Rock for Armadillo (no Opal deps)
    keycloak.py              # Keycloak OIDC (opt-in, with Armadillo)
  presets/
    __init__.py              # Named config templates (opal-dev, opal-prod, etc.)
  commands/
    setup.py                 # Interactive/non-interactive setup wizard
    lifecycle.py             # up, down, restart, status, reset, plan, validate
    auto_update.py           # Manual/scheduled health-checked image updates
    config.py                # Settings plus host maintenance schedule reconciliation
    certs.py                 # regenerate, info, ca-regenerate
    profiles.py              # add, remove, list
    instances.py             # create, list, info, remove
    backup.py                # create, restore, list
    volumes.py               # list, prune
    diagnose.py              # Stack health checks (containers, SSL, endpoints, databases)
    doctor.py                # Self-diagnostics (runtime, config, secrets, permissions)
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
  test_container_runtime.py  # Runtime selection and binding tests
  test_auto_update.py        # Update, verification, and rollback tests
  test_auto_update_scheduler.py # systemd/launchd rendering and lifecycle tests
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

**Opal flavor:** mongo, opal, nginx, certbot, rock (per profile), database (per db), agate, mailpit, mica, and elasticsearch.

**Armadillo flavor:** armadillo, armadillo-rock (per profile), nginx, certbot, and keycloak.

Automatic updates, scheduled backups, and profile pre-pulling are host jobs,
not services in the generated Compose model.

## Config Changes -> Regeneration

| Change | Regenerates |
|--------|-------------|
| Hosts | Certs + proxy metadata + NGINX + Compose |
| Port | Proxy metadata + NGINX + Compose |
| SSL strategy | Certs + NGINX + Compose |
| Version | Compose |
| Password | Compose |
| Automatic updates (`watchtower` legacy key) | Host schedule |
| Database add/remove | Compose |
| Agate enable/disable | Agate config + NGINX + Compose |
| Agate mail mode | Agate config + Compose |
| Mica enable/disable | Compose |
| Backup enable/disable | Host schedule |
| Profile updates enable/disable | Host schedule |
| Flavor change | Everything (different service set) |

## Volume Naming

All named volumes: `{stack_name}-{service}-data`. No collisions between instances.

## Flavors

Two deployment modes sharing the same architecture:

- **opal** (default): MongoDB + Opal + NGINX + Rock + optional Agate/Mica/databases
- **armadillo**: Armadillo (Parquet storage, no DB) + Rock + optional Keycloak

Both flavors share NGINX, Certbot, Rock profiles, and the host-side maintenance
scheduler.

## Container Runtime

`src/core/container_runtime.py` is the only abstraction that selects and runs a
container engine. Callers obtain a `ContainerRuntime` and use `run()`,
`compose()`, or `pull()`; they do not invoke `docker` or `podman` directly.

Selection is controlled by the global `--runtime auto|docker|podman` option or
`EASY_OPAL_RUNTIME`. An explicit choice probes only that runtime. `auto` first
reuses the runtime stored for the target instance; for an unbound instance it
probes complete engine + Compose pairs. Docker is the tie-breaker if both pairs
are usable. The resolved choice is persisted in the host-local registry so
installing another engine cannot silently move an existing deployment.

The registry binding currently identifies the engine family, not its context,
remote endpoint, graph root, or rootless/rootful storage. Operators must keep
that context stable. Cross-context or cross-engine moves require a newly
created instance; in-place rebinding is intentionally unsupported. Built-in
backup/restore transfers application and database data, not every ancillary
service volume.

Podman uses `podman compose` and requires Podman >= 4.6 plus
`podman-compose >= 1.6.0`. easy-opal sets
`PODMAN_COMPOSE_PROVIDER=podman-compose` so Podman's wrapper cannot select a
Docker-backed provider. Docker and Podman are alternatives: neither is a
Python package dependency, and selecting one never falls back to the other.

The generated Compose model is shared. Runtime-specific bind-mount details are
added during generation, but no generated service mounts an engine socket.
Automatic updates, scheduled backups, and scheduled profile pre-pulling call
the same runtime-neutral host commands under both Docker and Podman.

Podman reboot persistence is intentionally host-managed. `doctor` warns that
rootless production deployments need the user `podman-restart.service` (or an
equivalent generated systemd/Quadlet unit) plus lingering. easy-opal does not
change login or systemd policy automatically.

## Image Updates and Host Scheduling

`src/core/auto_update.py` updates only images referenced by running containers
in the target Compose project. `InstanceLock` serializes the operation. It
pulls mutable references first, leaves digest-pinned references unchanged,
compares resolved image IDs, and avoids a Compose restart when nothing changed.
It snapshots the complete generated Compose bytes before pulling and supplies a
private copy of that snapshot to apply/rollback. Any concurrent drift in the
live Compose file aborts the operation instead of being applied incidentally.
Changed images are applied only to the previously running service set with
`up -d --force-recreate --no-deps --pull never --wait`; stopped services and
dependencies remain stopped. The updater verifies the resulting service set,
image IDs, and health state.

A partial pull failure attempts to restore every previous mutable tag before
returning. A failed apply or verification retags the previous image IDs,
recreates the same service set, and verifies the result. This is a compensating
rollback limited to container image state. It is not atomic and does not undo
application writes, database/schema migrations, external effects,
configuration changes, or volume data. Superseded IDs are cleanup candidates
only after a healthy apply.

The historical `watchtower` configuration field and CLI alias remain for
compatibility; new commands can use `config auto-updates`. No Watchtower
service is registered. The archived
`containrrr/watchtower` image and Docker socket mount have been removed.

`src/core/auto_update_scheduler.py` manages three independent per-instance
jobs: health-checked image updates, application/database backups, and profile
image pre-pulling. It writes a user `launchd` agent on macOS, a user `systemd`
timer for non-root Linux, or a system timer when easy-opal is run as root.
Generated jobs invoke the current Python executable with an explicit
`--runtime`, instance name, `EASY_OPAL_HOME`, `PATH`, and captured Docker
context/host or Podman connection/host. This prevents runtime auto-detection
from drifting in an unattended session. Installation and removal are
idempotent, and scheduled commands do not start an intentionally stopped
stack.

The scheduler does not start Docker Desktop or a Podman machine; runtime
availability remains a host operational responsibility.
System-level schedules additionally fail closed unless Python, easy-opal,
manager/runtime/provider executables, configuration paths, and the local Unix
engine socket are root-controlled. Rootless and remote contexts must be
scheduled by their owning user.

Backup archives are assembled privately and published atomically only after
every component succeeds. Restore performs a complete structural/readiness
preflight before its first mutation (manifest, flavor, target mapping, payloads,
running containers, and tools). The subsequent restore remains component-wise,
not globally transactional; a later database failure cannot roll back an
earlier restored component, so production recovery requires a maintenance
window and post-restore verification.

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
- CSRF: current Opal SPA compatibility requires `CSRF_ALLOWED=*`; restrict
  exposure at NGINX/firewall boundaries and revisit when upstream supports a
  narrower origin policy
- Persistent CA: regenerating server cert preserves browser trust
- PEM validation on manual cert import
- Per-instance advisory locking (`fcntl.flock`)
- Host maintenance jobs do not mount Docker or Podman API sockets
- Let's Encrypt rollback to self-signed on failure
