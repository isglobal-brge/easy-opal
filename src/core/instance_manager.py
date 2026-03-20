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


def sync_registry() -> dict:
    """Sync registry with filesystem. Returns the synced registry.

    - Removes entries whose directories no longer exist.
    - Discovers directories not yet in the registry.
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
                "stack_name": None,
            }
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


class InstanceLock:
    """Simple file-based lock to prevent concurrent operations."""

    def __init__(self, ctx: InstanceContext):
        self.lock_path = ctx.root / ".lock"

    def __enter__(self):
        if self.lock_path.exists():
            # Check if the lock is stale (older than 10 minutes)
            age = datetime.now().timestamp() - self.lock_path.stat().st_mtime
            if age > 600:
                self.lock_path.unlink()
            else:
                pid = self.lock_path.read_text().strip()
                raise RuntimeError(
                    f"Instance is locked by another process (PID {pid}). "
                    f"If this is stale, delete {self.lock_path}"
                )
        self.lock_path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *args):
        if self.lock_path.exists():
            self.lock_path.unlink()


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
    """Create a new instance. Validates name, registers in registry."""
    err = validate_name(name)
    if err:
        raise ValueError(err)

    if path is not None:
        root = path / name
    else:
        root = _instances_dir() / name

    if root.exists():
        raise ValueError(f"Instance '{name}' already exists at {root}")

    ctx = InstanceContext(name=name, root=root)
    ctx.ensure_dirs()
    _register_instance(name, root)
    return ctx


def remove_instance(name: str, delete_data: bool = False) -> None:
    """Remove an instance from registry and optionally delete data."""
    registry = sync_registry()
    meta = registry.get("instances", {}).get(name)
    if not meta:
        raise ValueError(f"Instance '{name}' not found")

    root = Path(meta["path"])
    if delete_data and root.exists():
        shutil.rmtree(root)
    elif root.exists():
        for f in ["config.json", "secrets.env", "docker-compose.yml"]:
            p = root / f
            if p.exists():
                p.unlink()

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


def update_stack_name(name: str, stack_name: str) -> None:
    """Update the stack_name metadata for an instance."""
    registry = _load_registry()
    if name in registry.get("instances", {}):
        registry["instances"][name]["stack_name"] = stack_name
        _save_registry(registry)
