# Phase 1: Architectural Redesign Plan

---

## Scope

Phase 1 transforms easy-opal from a single-deployment setup script into a multi-instance deployment platform with a hermetic runtime, typed configuration, modular service architecture, and proper lifecycle management.

**What gets deleted:** `./setup` (~1850 lines bash), `./update`, `src/templates/docker-compose.yml.tpl`.
**What gets created:** 10 new Python modules, 1 new bash script.
**What gets rewritten:** config_manager, docker_manager, lifecycle_cmds, cli.py.

---

## Implementation Steps (in order)

### Step 1: Hermetic Bootstrap

**Goal:** A single `./easy-opal` script that auto-installs uv, manages its own Python, and requires zero manual setup. Clone the repo, run `./easy-opal` — it works.

**Why first:** Everything else depends on this. Without it, we can't bump Python, add Pydantic, or run from instance directories.

#### Changes

| Action | File | Details |
|--------|------|---------|
| REWRITE | `easy-opal` | ~50-line self-bootstrapping bash: ensures uv, delegates via `uv run --project $SCRIPT_DIR` |
| DELETE | `setup` | Replaced by bootstrap. Docker detection moves to Python CLI (already exists). |
| DELETE | `update` | Already replicated by `src/commands/update_cmd.py` |
| MODIFY | `pyproject.toml` | `requires-python = ">=3.11"`, add `pydantic>=2.0` |
| CREATE | `.python-version` | Contains `3.11` — uv auto-installs this |

#### New `./easy-opal` design

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Ensure uv is available
if ! command -v uv &>/dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

exec uv run --project "$SCRIPT_DIR" python -m src.cli "$@"
```

`uv run` does everything: installs Python 3.11 if missing, creates/syncs `.venv`, executes. The `--project` flag anchors to the repo so it works from any cwd.

#### Validation
- Fresh clone → `./easy-opal --help` works (uv auto-installs everything)
- Existing install → `./easy-opal` still works (uses existing .venv)

---

### Step 2: Pydantic Models + Secrets

**Goal:** Replace ad-hoc dict config with a versioned, typed Pydantic model. Separate secrets from config.

**Why second:** Multi-instance and service modules both consume `OpalConfig`. Getting the data model right first prevents cascading refactors later.

#### New files

**`src/core/models.py`** — All Pydantic models:

```python
class SSLConfig(BaseModel):
    strategy: Literal["self-signed", "letsencrypt", "manual", "none"] = "self-signed"
    cert_path: str = ""
    key_path: str = ""
    le_email: str = ""

class DatabaseConfig(BaseModel):
    type: Literal["postgres", "mysql", "mariadb"]
    name: str
    port: int
    user: str = "opal"
    database: str = "opaldata"
    version: str = "latest"

class ProfileConfig(BaseModel):
    name: str
    image: str = "datashield/rock-base"
    tag: str = "latest"

class WatchtowerConfig(BaseModel):
    enabled: bool = False
    poll_interval: int = 86400  # seconds internally
    cleanup: bool = True

class OpalConfig(BaseModel):
    schema_version: int = 1
    stack_name: str = "easy-opal"
    hosts: list[str] = ["localhost", "127.0.0.1"]
    opal_version: str = "latest"
    mongo_version: str = "latest"
    nginx_version: str = "latest"
    opal_external_port: int = 443
    opal_http_port: int = 8080
    profiles: list[ProfileConfig] = [ProfileConfig(name="rock")]
    databases: list[DatabaseConfig] = []
    watchtower: WatchtowerConfig = WatchtowerConfig()
    ssl: SSLConfig = SSLConfig()
```

**`src/core/secrets_manager.py`** — Handles `secrets.env`:

```python
def generate_password(length=24) -> str
def load_secrets(instance) -> dict[str, str]
def save_secrets(secrets: dict, instance) -> None
def ensure_secrets(instance) -> dict[str, str]  # generates missing ones
```

Generates random passwords for: `OPAL_ADMIN_PASSWORD`, `ROCK_ADMINISTRATOR_PASSWORD`, `ROCK_MANAGER_PASSWORD`, `ROCK_USER_PASSWORD`, plus one per database.

**`src/core/migration.py`** — Schema version handling:

```python
MIGRATIONS = {
    0: migrate_v0_to_v1,  # add schema_version, move passwords out
}

def migrate_if_needed(raw: dict) -> dict
```

#### Changes to existing files

| Action | File | Details |
|--------|------|---------|
| REWRITE | `src/core/config_manager.py` | `load_config()` returns `OpalConfig`, `save_config()` accepts `OpalConfig`. All path constants become functions accepting an instance root. Snapshot logic preserved. |
| MODIFY | All `src/commands/*.py` | `config["key"]` → `config.key`, `config.get("ssl", {}).get("strategy")` → `config.ssl.strategy` |
| MODIFY | `src/core/ssl_manager.py` | Accept paths as params instead of module-level constants. Persistent CA (load existing if present). File permissions `0o600` on keys. |

#### Validation
- `./easy-opal setup` produces a valid `config.json` with `schema_version: 1`
- Passwords in `secrets.env`, not in `config.json`
- Old `config.json` (no schema_version) auto-migrates on load

---

### Step 3: Multi-Instance

**Goal:** easy-opal manages N independent deployments, each in its own directory.

**Why third:** Requires typed config (Step 2) and hermetic bootstrap (Step 1).

#### New files

**`src/core/instance_manager.py`**:

```python
@dataclass
class InstanceContext:
    name: str
    root: Path              # ~/.easy-opal/instances/<name>/
    config_path: Path       # root / config.json
    secrets_path: Path      # root / secrets.env
    compose_path: Path      # root / docker-compose.yml
    data_dir: Path          # root / data/
    certs_dir: Path         # root / data/nginx/certs/
    backups_dir: Path       # root / backups/

def get_home() -> Path                          # ~/.easy-opal/ or $EASY_OPAL_HOME
def list_instances() -> list[str]
def create_instance(name, path=None) -> InstanceContext
def remove_instance(name, delete_data=False)
def resolve_instance(name: str | None) -> InstanceContext  # auto-detect if only one
```

**`src/commands/instance_cmds.py`**:

```
easy-opal instance create <name>        # creates in ~/.easy-opal/instances/<name>/
easy-opal instance create <name> --path /opt/opal   # custom location
easy-opal instance list                 # shows all instances with status
easy-opal instance remove <name>        # removes (asks about data)
easy-opal instance info <name>          # shows paths, config summary
```

#### Changes to existing files

| Action | File | Details |
|--------|------|---------|
| MODIFY | `src/cli.py` | Add `-i/--instance` global option. Resolve instance via `instance_manager`. Pass `InstanceContext` through Click context (`ctx.obj`). Add `instance` command group. |
| MODIFY | All core modules | Accept `InstanceContext` parameter instead of using module-level path constants |
| MODIFY | All command modules | Extract instance from `ctx.obj`, pass to core modules |

#### Instance resolution priority
1. `-i <name>` flag
2. `$EASY_OPAL_INSTANCE` env var
3. If exactly one instance exists, use it
4. Error: "multiple instances, specify with -i"

#### Migration for existing users
On first run after update: if `config.json` exists in the repo root, auto-migrate to `~/.easy-opal/instances/default/` and print a message.

#### Validation
- `easy-opal instance create staging` → creates directory structure
- `easy-opal -i staging setup` → runs setup in staging's directory
- `easy-opal instance list` → shows instances
- Running with no instances → prompts to create one

---

### Step 4: Service Registry

**Goal:** Each Docker service is a pluggable module. The compose file is assembled from modules, not mutated from a template.

**Why fourth:** Needs typed config (Step 2) and instance context (Step 3).

#### New directory: `src/services/`

**`src/services/__init__.py`** — Registry and protocol:

```python
class ServiceModule(Protocol):
    name: str
    def is_enabled(self, config: OpalConfig) -> bool: ...
    def compose_services(self, config: OpalConfig, ctx: InstanceContext) -> dict: ...
    def compose_volumes(self, config: OpalConfig) -> dict: ...
    def opal_env_vars(self, config: OpalConfig, secrets: dict) -> dict: ...
    def healthcheck(self) -> dict | None: ...
    def post_init(self, config: OpalConfig, ctx: InstanceContext) -> None: ...

class ServiceRegistry:
    def __init__(self, config: OpalConfig, ctx: InstanceContext, secrets: dict):
        # auto-registers all enabled modules
    def assemble_compose(self) -> dict:
        # merges all service fragments into one compose dict
    def run_post_init(self) -> None:
        # runs post_init hooks for all enabled modules
```

**Service modules (one file each):**

| Module | Always on? | Key responsibilities |
|--------|-----------|---------------------|
| `mongo.py` | Yes | Mongo service, healthcheck with `mongosh` |
| `opal.py` | Yes | Opal service, collects env vars from all modules, CSRF from hosts |
| `nginx.py` | Unless `ssl.strategy == "none"` | NGINX with SSL, volumes for certs/conf |
| `certbot.py` | Only if `letsencrypt` | Certbot with LE volumes |
| `rock.py` | For each profile | Rock service per profile, post-init hook for registration |
| `database.py` | For each database | Postgres/MySQL/MariaDB with healthcheck, Opal env vars |
| `watchtower.py` | If `watchtower.enabled` | Watchtower with docker.sock |

#### Changes to existing files

| Action | File | Details |
|--------|------|---------|
| REWRITE | `src/core/docker_manager.py` | `generate_compose_file()` becomes: instantiate `ServiceRegistry`, call `assemble_compose()`, write YAML. All Docker command functions stay. |
| DELETE | `src/templates/docker-compose.yml.tpl` | Replaced by programmatic assembly |
| KEEP | `src/templates/nginx.conf.tpl` | Still used by nginx_manager |
| KEEP | `src/templates/nginx-acme.conf.tpl` | Still used by nginx_manager |

#### Security hardening (integrated here)
- `opal.py`: `CSRF_ALLOWED` computed from `config.hosts` instead of `"*"`
- `rock.py`: reads Rock passwords from secrets, not hardcoded `"password"`
- `ssl_manager.py`: loads existing CA if present, only regenerates server cert
- All key files written with `0o600` permissions

#### Validation
- `easy-opal setup` produces functionally identical docker-compose.yml
- Adding/removing a database or profile regenerates correctly
- No `docker-compose.yml.tpl` needed

---

### Step 5: Idempotent Up + Healthchecks + Post-Init

**Goal:** `up` converges without restarting. Services declare healthchecks. Post-init hooks run after health passes.

**Why last:** Depends on service modules (Step 4) for healthchecks and hooks.

#### Changes

| Action | File | Details |
|--------|------|---------|
| REWRITE | `src/commands/lifecycle_cmds.py` | `up` = `docker compose up -d` (convergent), `restart` = `down` + `up` (explicit). Both wait for health and run post-init. |
| MODIFY | `src/core/docker_manager.py` | Add health-wait function: tries `--wait` flag, falls back to polling `docker compose ps` |
| MODIFY | `src/services/rock.py` | Post-init: call Opal API to verify Rock server registration |

#### Healthchecks per service

| Service | Check | Interval | Retries |
|---------|-------|----------|---------|
| mongo | `mongosh --eval "db.adminCommand('ping')"` | 10s | 5 |
| opal | `curl -sf http://localhost:8080/ > /dev/null` | 10s | 12 |
| nginx | `curl -sf http://localhost:80/ > /dev/null` | 5s | 3 |
| postgres | `pg_isready -U $user` | 5s | 5 |
| mysql/mariadb | `mysqladmin ping` | 5s | 5 |
| rock | `curl -sf http://localhost:8085/_check > /dev/null` | 10s | 12 |

#### New CLI semantics

```
easy-opal up          # Converge. Wait for health. Run post-init hooks.
easy-opal restart     # Full down + up cycle. Same wait + hooks.
easy-opal down        # Stop. (unchanged)
easy-opal status      # Show container status. (unchanged)
```

#### Validation
- `easy-opal up` twice in a row → second time is a no-op (no container restarts)
- After `up`, all healthchecks pass before the command returns
- Rock profiles are registered in Opal after post-init

---

## Dependency Graph

```
Step 1: Bootstrap
   └──→ Step 2: Pydantic Config + Secrets
           └──→ Step 3: Multi-Instance
                   └──→ Step 4: Service Registry + Security
                           └──→ Step 5: Idempotent Up + Health + Post-Init
```

Each step is independently testable and deployable. Users can use easy-opal at any step — it works, just without the features of later steps.

---

## Files Created (10 new)

```
src/core/models.py              # Pydantic config models
src/core/secrets_manager.py     # secrets.env handling
src/core/migration.py           # Schema version migrations
src/core/instance_manager.py    # Multi-instance CRUD
src/commands/instance_cmds.py   # instance create/list/remove/info
src/services/__init__.py        # ServiceRegistry + protocol
src/services/mongo.py           # MongoDB module
src/services/opal.py            # Opal module
src/services/nginx.py           # NGINX module
src/services/certbot.py         # Certbot module
src/services/rock.py            # Rock profile module
src/services/database.py        # PostgreSQL/MySQL/MariaDB module
src/services/watchtower.py      # Watchtower module
.python-version                 # "3.11"
```

## Files Deleted (3)

```
setup                           # Replaced by self-bootstrapping easy-opal
update                          # Replaced by update_cmd.py
src/templates/docker-compose.yml.tpl  # Replaced by service registry
```

## Files Rewritten (4)

```
easy-opal                       # New self-bootstrapping script
src/core/config_manager.py      # Pydantic-based, instance-aware
src/core/docker_manager.py      # Thin renderer using ServiceRegistry
src/commands/lifecycle_cmds.py   # Convergent up, explicit restart, health-wait
```

## Files Modified (10+)

```
pyproject.toml                  # requires-python, pydantic dep
src/cli.py                      # Global -i option, instance context
src/core/ssl_manager.py         # Persistent CA, file permissions
src/core/nginx_manager.py       # Instance-aware paths
src/commands/setup_cmd.py       # Random passwords, instance-aware
src/commands/config_cmds.py     # Attribute access, instance-aware
src/commands/cert_cmds.py       # ca-regenerate, instance-aware
src/commands/profile_cmds.py    # Instance-aware
src/commands/diagnostic_cmd.py  # Instance-aware
src/commands/update_cmd.py      # Instance-aware (tool update only)
```
