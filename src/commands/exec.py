"""Execute commands inside containers."""

import subprocess

import click

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.utils.console import error


@click.command(name="exec", context_settings={"ignore_unknown_options": True})
@click.argument("service")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def exec_cmd(ctx, service, command):
    """Execute a command inside a container.

    Examples:

      easy-opal exec opal bash

      easy-opal exec mongo mongosh

      easy-opal exec rock R
    """
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    config = load_config(instance)
    container = f"{config.stack_name}-{service}"

    cmd = ["docker", "exec", "-it", container] + list(command or ["sh"])

    try:
        subprocess.run(cmd, check=False)
    except FileNotFoundError:
        error("Docker not found.")
    except KeyboardInterrupt:
        pass
