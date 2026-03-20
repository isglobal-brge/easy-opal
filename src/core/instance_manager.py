"""Multi-instance management: create, list, remove, resolve."""

import os
import shutil
from pathlib import Path

from src.models.instance import InstanceContext


def get_home() -> Path:
    """Returns the easy-opal home directory (~/.easy-opal or $EASY_OPAL_HOME)."""
    return Path(os.environ.get("EASY_OPAL_HOME", Path.home() / ".easy-opal"))


def _instances_dir() -> Path:
    return get_home() / "instances"


def list_instances() -> list[str]:
    """Returns names of all instances."""
    d = _instances_dir()
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def create_instance(name: str, path: Path | None = None) -> InstanceContext:
    """Create a new instance directory structure."""
    if path is not None:
        root = path / name
    else:
        root = _instances_dir() / name

    if root.exists():
        raise ValueError(f"Instance '{name}' already exists at {root}")

    ctx = InstanceContext(name=name, root=root)
    ctx.ensure_dirs()
    return ctx


def remove_instance(name: str, delete_data: bool = False) -> None:
    """Remove an instance. If delete_data is False, only unlinks."""
    root = _instances_dir() / name
    if not root.exists():
        raise ValueError(f"Instance '{name}' not found")

    if delete_data:
        shutil.rmtree(root)
    else:
        # Remove config/compose but keep data/
        for f in ["config.json", "secrets.env", "docker-compose.yml"]:
            p = root / f
            if p.exists():
                p.unlink()


def get_instance(name: str) -> InstanceContext:
    """Get an existing instance by name."""
    root = _instances_dir() / name
    if not root.exists():
        raise ValueError(f"Instance '{name}' not found")
    return InstanceContext(name=name, root=root)


def resolve_instance(name: str | None) -> InstanceContext:
    """Resolve instance by name, $EASY_OPAL_INSTANCE, or auto-detect."""
    # Explicit name
    if name:
        return get_instance(name)

    # Environment variable
    env_name = os.environ.get("EASY_OPAL_INSTANCE")
    if env_name:
        return get_instance(env_name)

    # Auto-detect if only one exists
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
