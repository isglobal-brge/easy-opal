"""Docker volume management."""

import json
import subprocess

import click
from rich.table import Table
from rich.prompt import Confirm

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.utils.console import console, success, error, info, dim, warning


def _get_project_volumes(stack_name: str) -> list[dict]:
    """Get Docker volumes belonging to this stack."""
    try:
        result = subprocess.run(
            ["docker", "volume", "ls", "--format", "json",
             "--filter", f"label=com.docker.compose.project={stack_name}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return []

        volumes = []
        for line in result.stdout.strip().splitlines():
            if line.strip():
                volumes.append(json.loads(line))
        return volumes
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _get_volume_size(name: str) -> str:
    """Get the disk usage of a volume."""
    try:
        result = subprocess.run(
            ["docker", "system", "df", "-v", "--format", "json"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        # docker system df -v --format json outputs one JSON per line for volumes
        for line in result.stdout.strip().splitlines():
            try:
                data = json.loads(line)
                # Format varies by Docker version
                if isinstance(data, dict) and data.get("Name") == name:
                    return data.get("Size", "?")
                if isinstance(data, dict) and "Volumes" in data:
                    for v in data["Volumes"]:
                        if v.get("Name") == name:
                            return v.get("Size", "?")
            except json.JSONDecodeError:
                continue
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "?"


@click.group(name="volumes")
def volumes():
    """Manage Docker volumes."""
    pass


@volumes.command(name="list")
@click.pass_context
def list_volumes(ctx):
    """List Docker volumes for this instance."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)
    vols = _get_project_volumes(cfg.stack_name)

    if not vols:
        dim("No volumes found. Is the stack running?")
        return

    table = Table(title=f"Volumes ({cfg.stack_name})")
    table.add_column("Name", style="cyan")
    table.add_column("Driver", style="dim")

    for v in vols:
        name = v.get("Name", v.get("name", "?"))
        driver = v.get("Driver", v.get("driver", "local"))
        table.add_row(name, driver)

    console.print(table)
    dim(f"\n{len(vols)} volume(s) total.")


@volumes.command()
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def prune(ctx, yes):
    """Remove unused volumes for this instance."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)

    if not yes:
        warning("This will remove ALL unused volumes for this stack.")
        if not Confirm.ask("Continue?", default=False):
            return

    # Stop stack first, then remove volumes
    from src.core.docker import compose_down
    compose_down(instance, cfg)

    result = subprocess.run(
        ["docker", "volume", "prune", "--filter",
         f"label=com.docker.compose.project={cfg.stack_name}", "-f"],
        capture_output=True, text=True, check=False,
    )

    if result.returncode == 0:
        success("Unused volumes pruned.")
        if result.stdout.strip():
            dim(result.stdout.strip())
    else:
        error(f"Prune failed: {result.stderr[:200]}")
