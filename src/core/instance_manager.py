"""Multi-instance management with persistent registry and auto-sync."""

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.models.instance import InstanceContext

VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


# ── Paths ────────────────────────────────────────────────────────────────────


def get_home() -> Path:
    """Returns ~/.easy-opal or $EASY_OPAL_HOME."""
    return Path(os.environ.get("EASY_OPAL_HOME", Path.home() / ".easy-opal"))


def _instances_dir() -> Path:
    d = get_home() / "instances"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return get_home() / "registry.json"


# ── Registry ─────────────────────────────────────────────────────────────────


def _load_registry() -> dict:
    path = _registry_path()
    if not path.exists():
        return {"version": 1, "instances": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "instances": {}}


def _save_registry(registry: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_stack_name(path: Path) -> str | None:
    """Read stack_name from an instance's config.json without side effects.

    config.json is the source of truth for the Docker Compose project name; the
    registry only mirrors it. Reading raw JSON avoids load_config()'s side effect
    of creating a default config for unconfigured directories.
    """
    cfg_path = path / "config.json"
    if not cfg_path.exists():
        return None
    try:
        return json.loads(cfg_path.read_text()).get("stack_name")
    except (json.JSONDecodeError, OSError):
        return None


def sync_registry() -> dict:
    """Sync registry with filesystem. Returns the synced registry.

    - Removes entries whose directories no longer exist.
    - Discovers directories not yet in the registry.
    - Backfills/refreshes each entry's stack_name from its config.json so the
      registry never disagrees with the real Docker project name.
    """
    registry = _load_registry()
    instances = registry.setdefault("instances", {})
    changed = False

    # Remove stale entries
    stale = [
        name for name, meta in instances.items()
        if not Path(meta["path"]).exists()
    ]
    for name in stale:
        del instances[name]
        changed = True

    # Discover new directories
    inst_dir = _instances_dir()
    for p in inst_dir.iterdir():
        if p.is_dir() and p.name not in instances:
            instances[p.name] = {
                "path": str(p),
                "created_at": _now_iso(),
                "last_accessed": _now_iso(),
                "stack_name": _read_stack_name(p),
            }
            changed = True

    # Backfill/refresh stack_name from config.json (the source of truth)
    for meta in instances.values():
        real = _read_stack_name(Path(meta["path"]))
        if real and meta.get("stack_name") != real:
            meta["stack_name"] = real
            changed = True

    if changed:
        _save_registry(registry)

    return registry


def _touch_instance(name: str) -> None:
    """Update last_accessed timestamp for an instance."""
    registry = _load_registry()
    if name in registry.get("instances", {}):
        registry["instances"][name]["last_accessed"] = _now_iso()
        _save_registry(registry)


def _register_instance(name: str, path: Path, stack_name: str | None = None) -> None:
    registry = _load_registry()
    registry.setdefault("instances", {})[name] = {
        "path": str(path),
        "created_at": _now_iso(),
        "last_accessed": _now_iso(),
        "stack_name": stack_name,
    }
    _save_registry(registry)


def _unregister_instance(name: str) -> None:
    registry = _load_registry()
    registry.get("instances", {}).pop(name, None)
    _save_registry(registry)


# ── Validation ───────────────────────────────────────────────────────────────


def validate_name(name: str) -> str | None:
    """Returns an error message if the name is invalid, None if OK."""
    if not name:
        return "Name cannot be empty."
    if len(name) > 64:
        return "Name too long (max 64 characters)."
    if not VALID_NAME_RE.match(name):
        return "Name must start with a letter/number and contain only letters, numbers, dots, hyphens, or underscores."
    return None


# ── Lock ─────────────────────────────────────────────────────────────────────


LOCK_TIMEOUT_SECONDS = 600  # 10 minutes


class InstanceLock:
    """File-based lock using fcntl for atomic locking on Unix."""

    def __init__(self, ctx: InstanceContext):
        self.lock_path = ctx.root / ".lock"
        self._fd = None

    def __enter__(self):
        import fcntl

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.lock_path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._fd.close()
            self._fd = None
            raise RuntimeError(
                f"Instance is locked by another process. "
                f"If this is stale, delete {self.lock_path}"
            )
        self._fd.write(str(os.getpid()))
        self._fd.flush()
        return self

    def __exit__(self, *args):
        if self._fd:
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None
        if self.lock_path.exists():
            try:
                self.lock_path.unlink()
            except OSError:
                pass


# ── CRUD ─────────────────────────────────────────────────────────────────────


def list_instances() -> list[str]:
    """Returns names of all instances (synced with filesystem)."""
    registry = sync_registry()
    return sorted(registry.get("instances", {}).keys())


def get_registry_info() -> dict[str, dict]:
    """Returns full registry info for all instances."""
    registry = sync_registry()
    return registry.get("instances", {})


def create_instance(name: str, path: Path | None = None) -> InstanceContext:
    """Create a new instance. Validates name, checks collisions, registers it.

    The instance name doubles as the Docker stack name, so it must not collide
    with another instance's stack name either.
    """
    err = validate_name(name)
    if err:
        raise ValueError(err)

    taken_by = is_stack_name_taken(name)
    if taken_by and taken_by != name:
        raise ValueError(
            f"Name '{name}' is already used as the stack name of instance "
            f"'{taken_by}'. Choose a different name."
        )

    if path is not None:
        root = path / name
    else:
        root = _instances_dir() / name

    if root.exists():
        raise ValueError(f"Instance '{name}' already exists at {root}")

    ctx = InstanceContext(name=name, root=root)
    ctx.ensure_dirs()
    _register_instance(name, root, stack_name=name)
    return ctx


def remove_instance(name: str, delete_data: bool = False) -> None:
    """Remove an instance: stop containers, optionally delete volumes and data."""
    import subprocess

    registry = sync_registry()
    meta = registry.get("instances", {}).get(name)
    if not meta:
        raise ValueError(f"Instance '{name}' not found")

    root = Path(meta["path"])
    compose_file = root / "docker-compose.yml"
    # config.json is the authoritative source for the Docker project name;
    # fall back to the registry mirror, then the instance name. Never skip
    # teardown just because the registry's cached stack_name is missing.
    stack_name = _read_stack_name(root) or meta.get("stack_name") or name

    # Stop containers (and remove volumes if delete_data)
    if compose_file.exists():
        down_args = ["docker", "compose", "--project-name", stack_name,
                     "-f", str(compose_file), "down"]
        if delete_data:
            down_args.append("-v")
        subprocess.run(down_args, capture_output=True, check=False, timeout=120)

    # Remove the instance directory entirely so it is not rediscovered on the
    # next sync. Docker named volumes are preserved unless --delete-data added
    # the -v flag to the compose down above.
    if root.exists():
        shutil.rmtree(root)

    _unregister_instance(name)


def get_instance(name: str) -> InstanceContext:
    """Get an existing instance by name."""
    registry = sync_registry()
    meta = registry.get("instances", {}).get(name)
    if not meta:
        raise ValueError(f"Instance '{name}' not found")

    root = Path(meta["path"])
    if not root.exists():
        _unregister_instance(name)
        raise ValueError(f"Instance '{name}' directory is missing (cleaned from registry)")

    _touch_instance(name)
    return InstanceContext(name=name, root=root)


def resolve_instance(name: str | None) -> InstanceContext:
    """Resolve by explicit name, $EASY_OPAL_INSTANCE, or auto-detect."""
    if name:
        return get_instance(name)

    env_name = os.environ.get("EASY_OPAL_INSTANCE")
    if env_name:
        return get_instance(env_name)

    instances = list_instances()
    if len(instances) == 1:
        return get_instance(instances[0])
    elif len(instances) == 0:
        raise ValueError(
            "No instances found. Create one with: easy-opal instance create <name>"
        )
    else:
        names = ", ".join(instances)
        raise ValueError(
            f"Multiple instances found ({names}). Specify one with: easy-opal -i <name>"
        )


def next_available_name(prefix: str) -> str:
    """Find the next free name like opal1, opal2, avoiding both instance names
    and stack names (they share a namespace since a new instance's name is also
    its stack name)."""
    registry = sync_registry()
    used = set(registry.get("instances", {}).keys())
    for meta in registry.get("instances", {}).values():
        if meta.get("stack_name"):
            used.add(meta["stack_name"])
        real = _read_stack_name(Path(meta["path"]))
        if real:
            used.add(real)
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def is_stack_name_taken(stack_name: str, exclude_instance: str | None = None) -> str | None:
    """Returns the instance whose stack uses this name, or None if available.

    Reads each instance's config.json (authoritative) and falls back to the
    registry's cached stack_name, so collisions are caught even for instances
    discovered on disk or whose registry entry is stale.
    """
    registry = sync_registry()
    for inst_name, meta in registry.get("instances", {}).items():
        if inst_name == exclude_instance:
            continue
        used = _read_stack_name(Path(meta["path"])) or meta.get("stack_name")
        if used == stack_name:
            return inst_name
    return None


def update_stack_name(name: str, stack_name: str) -> None:
    """Update the stack_name metadata for an instance."""
    registry = _load_registry()
    if name in registry.get("instances", {}):
        registry["instances"][name]["stack_name"] = stack_name
        _save_registry(registry)
