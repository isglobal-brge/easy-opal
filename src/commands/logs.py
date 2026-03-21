"""View container logs."""

import subprocess

import click

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.utils.console import error


@click.command()
@click.argument("service", default="opal")
@click.option("-f", "--follow", is_flag=True, help="Follow log output.")
@click.option("-n", "--tail", "lines", default=50, help="Number of lines to show.")
@click.pass_context
def logs(ctx, service, follow, lines):
    """View logs for a service (opal, mongo, nginx, rock, etc.)."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    config = load_config(instance)
    container = f"{config.stack_name}-{service}"

    cmd = ["docker", "logs", container, "--tail", str(lines)]
    if follow:
        cmd.append("-f")

    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        error("Docker not found.")
    except KeyboardInterrupt:
        pass
