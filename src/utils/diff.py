"""Config diff and dry-run utilities."""

from src.models.config import OpalConfig
from src.utils.console import console


def show_config_diff(old: OpalConfig, new: OpalConfig) -> bool:
    """Display differences between two configs. Returns True if there are changes."""
    old_d = old.model_dump()
    new_d = new.model_dump()
    changes = _diff_dicts(old_d, new_d)

    if not changes:
        console.print("[dim]No changes.[/dim]")
        return False

    for path, (old_val, new_val) in changes.items():
        console.print(f"  [cyan]{path}[/cyan]: [red]{old_val}[/red] -> [green]{new_val}[/green]")
    return True


def _diff_dicts(old: dict, new: dict, prefix: str = "") -> dict:
    """Recursively diff two dicts. Returns {path: (old_val, new_val)}."""
    changes = {}
    all_keys = set(old.keys()) | set(new.keys())

    for key in sorted(all_keys):
        path = f"{prefix}.{key}" if prefix else key
        old_val = old.get(key)
        new_val = new.get(key)

        if old_val == new_val:
            continue

        if isinstance(old_val, dict) and isinstance(new_val, dict):
            changes.update(_diff_dicts(old_val, new_val, path))
        elif isinstance(old_val, list) and isinstance(new_val, list):
            if old_val != new_val:
                changes[path] = (old_val, new_val)
        else:
            changes[path] = (old_val, new_val)

    return changes


def show_compose_preview(config: OpalConfig, ctx) -> None:
    """Generate and display compose without writing to disk."""
    from src.core.secrets_manager import ensure_secrets
    from src.services import ServiceRegistry
    import yaml

    secrets = ensure_secrets(ctx, config)
    registry = ServiceRegistry(config, ctx, secrets)
    compose = registry.assemble_compose()

    console.print("\n[bold]Generated docker-compose.yml:[/bold]\n")
    console.print(yaml.dump(compose, default_flow_style=False, sort_keys=False))
