"""Instance management commands: create, list, remove, info."""

import json
import subprocess
from pathlib import Path

import click
from rich.table import Table
from rich.panel import Panel

from src.core import instance_manager
from src.core.config_manager import config_exists, load_config
from src.core.container_runtime import (
    RuntimeSelectionError,
    get_runtime,
    get_runtime_choice,
)
from src.core.secrets_manager import load_secrets
from src.core.ssl import get_cert_info
from src.utils.console import console, success, error, dim


_RUNTIME_ERROR_KEY = "__runtime_error__"


def _json_records(output: str) -> list[dict]:
    """Accept both JSON arrays and newline-delimited JSON from Compose providers."""
    output = output.strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass
    records = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _get_container_status(instance, stack_name: str) -> dict[str, str]:
    """Query the selected runtime for container statuses of a stack."""
    try:
        runtime = get_runtime(instance)
        result = runtime.compose(
            ["ps", "--format", "json"], instance, project_name=stack_name,
            capture_output=True, text=True, check=False, timeout=5,
        )
        if result.returncode != 0:
            return {}

        statuses = {}
        for c in _json_records(result.stdout):
            name = c.get("Name", c.get("name", c.get("Names", "?")))
            if isinstance(name, list):
                name = name[0] if name else "?"
            status_detail = c.get("Status", c.get("status", ""))
            state = c.get("State", c.get("state", status_detail or "?"))
            health = c.get("Health", c.get("health", ""))
            if not health and "healthy" in str(status_detail).lower():
                health = "healthy"
            short_name = name.replace(f"{stack_name}-", "")
            label = state
            if health:
                label = f"{state} ({health})"
            statuses[short_name] = label
        return statuses
    except RuntimeSelectionError as exc:
        return {_RUNTIME_ERROR_KEY: str(exc)}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}


def _get_runtime_name(instance) -> str:
    return instance_manager.get_instance_runtime(instance) or "unbound"


def _status_summary(statuses: dict[str, str]) -> str:
    """Summarize container statuses into a short string."""
    if _RUNTIME_ERROR_KEY in statuses:
        return "[red]runtime unavailable[/red]"
    if not statuses:
        return "[dim]stopped[/dim]"

    running = sum(1 for s in statuses.values() if "running" in s.lower())
    healthy = sum(1 for s in statuses.values() if "healthy" in s.lower())
    total = len(statuses)

    if healthy == total:
        return f"[green]{total}/{total} healthy[/green]"
    elif running == total:
        return f"[yellow]{running}/{total} running[/yellow]"
    elif running > 0:
        return f"[yellow]{running}/{total} running[/yellow]"
    else:
        return "[red]all stopped[/red]"


@click.group()
def instance():
    """Manage easy-opal instances / stacks (independent deployments).

    Also available as 'easy-opal stack'. An instance's name is also its container
    stack (Compose project) name.
    """
    pass


@instance.command(name="list")
def list_cmd():
    """List all instances with status."""
    registry_info = instance_manager.get_registry_info()
    if not registry_info:
        dim("No instances found. Create one with: easy-opal instance create <name>")
        return

    table = Table(title="Instances")
    table.add_column("Name", style="cyan bold")
    table.add_column("Stack")
    table.add_column("SSL")
    table.add_column("Services")
    table.add_column("Runtime")
    table.add_column("Containers")
    table.add_column("Last used", style="dim")

    for name, meta in sorted(registry_info.items()):
        path = Path(meta["path"])
        accessed = (meta.get("last_accessed") or "?")[:10]

        if not path.exists():
            table.add_row(name, "-", "-", "-", "-", "[red]missing[/red]", accessed)
            continue

        try:
            ctx = instance_manager.get_instance(name)
        except ValueError:
            table.add_row(name, "-", "-", "-", "-", "[red]error[/red]", accessed)
            continue

        if not config_exists(ctx):
            table.add_row(
                name,
                "-",
                "-",
                "-",
                _get_runtime_name(ctx),
                "[yellow]not configured[/yellow]",
                accessed,
            )
            continue

        cfg = load_config(ctx)
        ssl = str(cfg.ssl.strategy.value)
        services = []
        services.append(cfg.flavor)
        if cfg.agate.enabled:
            services.append("agate")
        if cfg.mica.enabled:
            services.append("mica")
        if cfg.databases:
            services.append(f"{len(cfg.databases)} db")
        services_str = ", ".join(services)

        runtime_name = _get_runtime_name(ctx)
        statuses = (
            _get_container_status(ctx, cfg.stack_name)
            if runtime_name != "unbound"
            else {}
        )
        containers = _status_summary(statuses)

        table.add_row(name, cfg.stack_name, ssl, services_str, runtime_name, containers, accessed)

    console.print(table)


@instance.command()
@click.argument("name")
@click.option("--path", type=click.Path(), default=None, help="Custom parent directory.")
def create(name: str, path: str | None):
    """Create a new instance."""
    try:
        runtime = None
        if get_runtime_choice() != "auto":
            runtime = get_runtime()

        created = instance_manager.create_instance(
            name, Path(path) if path else None
        )
        if runtime is not None:
            instance_manager.set_instance_runtime(created, runtime.name)
        success(f"Instance '{name}' created at {created.root}")
        console.print(f"Run [bold]easy-opal -i {name} setup[/bold] to configure it.")
    except (RuntimeSelectionError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@instance.command()
@click.argument("name")
@click.option(
    "--delete-data",
    is_flag=True,
    help="Also delete named volumes; local config/backups are always deleted.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def remove(name: str, delete_data: bool, yes: bool):
    """Remove an instance (stops containers, optionally deletes volumes)."""
    if not yes:
        if delete_data:
            action = ("stop containers and delete the instance directory AND its "
                      "named container volumes (all data) for")
        else:
            action = ("stop containers and delete the instance directory "
                      "(config, backups, certificates) for")
        if not click.confirm(f"Are you sure you want to {action} instance '{name}'?"):
            console.print("Aborted.")
            return

    try:
        instance_manager.remove_instance(name, delete_data=delete_data)
        if delete_data:
            msg = f"Instance '{name}' removed, including its named container volumes."
        else:
            msg = (f"Instance '{name}' removed (config, backups, and certificates deleted). "
                   f"Named container volumes were preserved — use --delete-data to also remove them.")
        success(msg)
    except (ValueError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


@instance.command()
@click.argument("name")
def info(name: str):
    """Show detailed instance information."""
    try:
        ctx = instance_manager.get_instance(name)
    except ValueError as e:
        error(str(e))
        return

    # Header
    console.print(Panel(f"[bold cyan]{name}[/bold cyan]", subtitle=str(ctx.root)))

    if not config_exists(ctx):
        console.print(f"[bold]Runtime:[/bold] {_get_runtime_name(ctx)}")
        console.print(
            f"[yellow]Not configured. Run: easy-opal -i {name} setup[/yellow]"
        )
        return

    cfg = load_config(ctx)

    # Config table
    config_table = Table(show_header=False, box=None, padding=(0, 2))
    config_table.add_column("Key", style="bold")
    config_table.add_column("Value")

    config_table.add_row("Stack", cfg.stack_name)
    config_table.add_row("Runtime", _get_runtime_name(ctx))
    config_table.add_row("SSL", cfg.ssl.strategy.value)
    config_table.add_row("Hosts", ", ".join(cfg.hosts) if cfg.hosts else "(none)")
    config_table.add_row("Opal", cfg.opal_version)
    config_table.add_row("MongoDB", cfg.mongo_version)
    config_table.add_row("Profiles", ", ".join(f"{p.name} ({p.image}:{p.tag})" for p in cfg.profiles))

    if cfg.databases:
        for db in cfg.databases:
            mode = "external" if db.external else f"port {db.port}"
            config_table.add_row(f"DB: {db.name}", f"{db.type.value} ({mode})")

    if cfg.agate.enabled:
        config_table.add_row("Agate", f"{cfg.agate.version} (mail: {cfg.agate.mail_mode})")
    if cfg.mica.enabled:
        config_table.add_row("Mica", f"{cfg.mica.version} (ES: {cfg.mica.elasticsearch_version})")
    if cfg.watchtower.enabled:
        config_table.add_row(
            "Automatic updates", f"every {cfg.watchtower.poll_interval_hours}h"
        )

    console.print(config_table)

    # SSL cert info
    cert = get_cert_info(ctx)
    if cert:
        console.print(f"\n[bold]Certificate:[/bold] expires {cert['not_after'][:10]}, SANs: {', '.join(cert['dns_names'])}")

    # Container status
    statuses = (
        _get_container_status(ctx, cfg.stack_name)
        if _get_runtime_name(ctx) != "unbound"
        else {}
    )
    if _RUNTIME_ERROR_KEY in statuses:
        console.print(
            "\n[red]Containers unavailable:[/red] "
            f"{statuses[_RUNTIME_ERROR_KEY]}"
        )
    elif statuses:
        console.print(f"\n[bold]Containers:[/bold]")
        for svc, status in sorted(statuses.items()):
            if "healthy" in status.lower():
                icon = "[green]up[/green]"
            elif "running" in status.lower():
                icon = "[yellow]up[/yellow]"
            else:
                icon = "[red]down[/red]"
            console.print(f"  {icon}  {svc}: {status}")
    else:
        console.print("\n[dim]Containers: not running[/dim]")

    # Secrets summary
    secrets = load_secrets(ctx)
    if secrets:
        console.print(f"\n[bold]Secrets:[/bold] {len(secrets)} keys in {ctx.secrets_path}")
