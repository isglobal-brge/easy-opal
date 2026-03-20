"""CLI entry point. Routes all commands and manages instance context."""

import sys

import click

from src.core.instance_manager import resolve_instance, list_instances


@click.group()
@click.option("-i", "--instance", "instance_name", envvar="EASY_OPAL_INSTANCE", default=None,
              help="Target instance name (auto-detected if only one exists).")
@click.pass_context
def main(ctx, instance_name):
    """easy-opal — deploy and manage OBiBa Opal environments."""
    ctx.ensure_object(dict)

    # Instance commands don't need a resolved instance
    if ctx.invoked_subcommand == "instance":
        return

    # For all other commands, resolve the instance
    try:
        ctx.obj["instance"] = resolve_instance(instance_name)
    except ValueError as e:
        # If no instances exist and user is running setup, create a default one
        if ctx.invoked_subcommand == "setup" and not list_instances():
            from src.core.instance_manager import create_instance
            ctx.obj["instance"] = create_instance("default")
        else:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


# Register commands
from src.commands.instances import instance
from src.commands.setup import setup
from src.commands.lifecycle import up, down, restart, status, reset
from src.commands.config import config
from src.commands.certs import cert
from src.commands.profiles import profile
from src.commands.diagnose import diagnose
from src.commands.update import update

main.add_command(instance)
main.add_command(setup)
main.add_command(up)
main.add_command(down)
main.add_command(restart)
main.add_command(status)
main.add_command(reset)
main.add_command(config)
main.add_command(cert)
main.add_command(profile)
main.add_command(diagnose)
main.add_command(update)
