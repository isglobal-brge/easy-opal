"""Instance management commands: create, list, remove, info."""

import click
from rich.table import Table

from src.core import instance_manager
from src.core.config_manager import config_exists, load_config
from src.utils.console import console, success, error


@click.group()
def instance():
    """Manage easy-opal instances (independent deployments)."""
    pass


@instance.command()
@click.argument("name")
@click.option("--path", type=click.Path(), default=None, help="Custom parent directory.")
def create(name: str, path: str | None):
    """Create a new instance."""
    from pathlib import Path

    try:
        ctx = instance_manager.create_instance(name, Path(path) if path else None)
        success(f"Instance '{name}' created at {ctx.root}")
        console.print(f"Run [bold]easy-opal -i {name} setup[/bold] to configure it.")
    except ValueError as e:
        error(str(e))


@instance.command(name="list")
def list_cmd():
    """List all instances."""
    registry_info = instance_manager.get_registry_info()
    if not registry_info:
        console.print("[dim]No instances found. Create one with: easy-opal instance create <name>[/dim]")
        return

    table = Table(title="Instances")
    table.add_column("Name", style="cyan")
    table.add_column("Stack", style="bold")
    table.add_column("Created", style="dim")
    table.add_column("Last used", style="dim")
    table.add_column("Status")

    for name, meta in sorted(registry_info.items()):
        from pathlib import Path
        path = Path(meta["path"])
        stack = meta.get("stack_name") or "-"
        created = (meta.get("created_at") or "?")[:10]
        accessed = (meta.get("last_accessed") or "?")[:10]

        if not path.exists():
            status = "[red]missing[/red]"
        elif config_exists(instance_manager.get_instance(name)):
            status = "[green]configured[/green]"
        else:
            status = "[yellow]not configured[/yellow]"

        table.add_row(name, stack, created, accessed, status)

    console.print(table)


@instance.command()
@click.argument("name")
@click.option("--delete-data", is_flag=True, help="Also delete all data and volumes.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def remove(name: str, delete_data: bool, yes: bool):
    """Remove an instance."""
    if not yes:
        action = "delete all data for" if delete_data else "remove config for"
        if not click.confirm(f"Are you sure you want to {action} instance '{name}'?"):
            console.print("Aborted.")
            return

    try:
        instance_manager.remove_instance(name, delete_data=delete_data)
        success(f"Instance '{name}' removed.")
    except ValueError as e:
        error(str(e))


@instance.command()
@click.argument("name")
def info(name: str):
    """Show instance details."""
    try:
        ctx = instance_manager.get_instance(name)
    except ValueError as e:
        error(str(e))
        return

    console.print(f"[bold]Instance:[/bold] {ctx.name}")
    console.print(f"  Root:       {ctx.root}")
    console.print(f"  Config:     {ctx.config_path} ({'exists' if ctx.config_path.exists() else 'missing'})")
    console.print(f"  Secrets:    {ctx.secrets_path} ({'exists' if ctx.secrets_path.exists() else 'missing'})")
    console.print(f"  Compose:    {ctx.compose_path} ({'exists' if ctx.compose_path.exists() else 'missing'})")
    console.print(f"  Data:       {ctx.data_dir}")

    if config_exists(ctx):
        cfg = load_config(ctx)
        console.print(f"\n[bold]Configuration:[/bold]")
        console.print(f"  Stack:      {cfg.stack_name}")
        console.print(f"  SSL:        {cfg.ssl.strategy}")
        console.print(f"  Opal:       {cfg.opal_version}")
        console.print(f"  MongoDB:    {cfg.mongo_version}")
        console.print(f"  Profiles:   {', '.join(p.name for p in cfg.profiles)}")
        if cfg.databases:
            console.print(f"  Databases:  {', '.join(f'{d.name} ({d.type})' for d in cfg.databases)}")
