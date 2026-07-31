"""Container volume management."""

import json

import click
from rich.table import Table
from rich.prompt import Confirm

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.core.instance_manager import InstanceLock
from src.utils.console import console, success, error, dim, warning, require_single_instance


def _get_project_volumes(instance: InstanceContext, stack_name: str) -> list[dict]:
    """Get container volumes belonging to this stack."""
    try:
        runtime = get_runtime(instance)
        volumes: dict[str, dict] = {}
        for project_label in (
            "com.docker.compose.project",
            "io.podman.compose.project",
        ):
            result = runtime.run(
                [
                    "volume",
                    "ls",
                    "--format",
                    "json",
                    "--filter",
                    f"label={project_label}={stack_name}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                suffix = f": {detail}" if detail else ""
                raise click.ClickException(
                    "Could not list project volumes "
                    f"(exit code {result.returncode}){suffix}"
                )
            if not result.stdout.strip():
                continue

            try:
                parsed = json.loads(result.stdout)
            except json.JSONDecodeError:
                parsed = []
                for line in result.stdout.splitlines():
                    if not line.strip():
                        continue
                    try:
                        parsed.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise click.ClickException(
                            "Could not list project volumes: runtime returned invalid JSON."
                        ) from exc
            if isinstance(parsed, dict):
                parsed = [parsed]
            for item in parsed if isinstance(parsed, list) else []:
                if isinstance(item, dict):
                    name = item.get("Name", item.get("name", repr(item)))
                    volumes[name] = item
        return list(volumes.values())
    except RuntimeSelectionError as exc:
        raise click.ClickException(str(exc)) from exc
    except OSError as exc:
        raise click.ClickException(
            f"Could not list project volumes: {exc}"
        ) from exc


@click.group(name="volumes")
def volumes():
    """Manage container volumes."""
    pass


@volumes.command(name="list")
@click.pass_context
def list_volumes(ctx):
    """List container volumes for this instance."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)
    vols = _get_project_volumes(instance, cfg.stack_name)

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
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)

    if not yes:
        warning("This will remove ALL unused volumes for this stack.")
        if not Confirm.ask("Continue?", default=False):
            return

    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    # Stop stack first, then remove volumes
    runtime = get_runtime(instance)
    down = runtime.compose(
        ["down"], instance, project_name=cfg.stack_name, check=False
    )
    if down.returncode != 0:
        raise click.ClickException(
            "Could not stop the stack; no volumes were removed."
        )

    names = [
        volume.get("Name", volume.get("name"))
        for volume in _get_project_volumes(instance, cfg.stack_name)
    ]
    names = [name for name in names if name]
    if not names:
        dim("No project volumes found.")
        return

    result = runtime.run(
        ["volume", "rm", *names], capture_output=True, text=True, check=False
    )

    if result.returncode == 0:
        success("Project volumes removed.")
        if result.stdout.strip():
            dim(result.stdout.strip())
    else:
        raise click.ClickException(f"Prune failed: {result.stderr[:200]}")
