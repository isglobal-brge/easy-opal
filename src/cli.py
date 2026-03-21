"""CLI entry point. Routes all commands and manages instance context."""

import sys

import click

from src.core.instance_manager import resolve_instance, list_instances, get_instance


class EasyOpalGroup(click.Group):
    """Custom group with clean exception handling."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit:
            raise
        except click.exceptions.Abort:
            raise
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


@click.group(cls=EasyOpalGroup)
@click.option("-i", "--instance", "instance_name", envvar="EASY_OPAL_INSTANCE", default=None,
              help="Target instance (auto-detected if only one exists).")
@click.option("--all", "all_instances", is_flag=True, help="Apply to all instances.")
@click.pass_context
def main(ctx, instance_name, all_instances):
    """Deploy and manage OBiBa Opal environments."""
    ctx.ensure_object(dict)
    ctx.obj["all"] = all_instances

    # Instance commands don't need a resolved instance
    if ctx.invoked_subcommand == "instance":
        return

    if all_instances:
        names = list_instances()
        if not names:
            click.echo("Error: No instances found.", err=True)
            sys.exit(1)
        ctx.obj["instances"] = [get_instance(n) for n in names]
        ctx.obj["instance"] = ctx.obj["instances"][0]
        return

    # Multiple instances: -i opal1,opal2
    if instance_name and "," in instance_name:
        names = [n.strip() for n in instance_name.split(",") if n.strip()]
        ctx.obj["instances"] = [get_instance(n) for n in names]
        ctx.obj["instance"] = ctx.obj["instances"][0]
        return

    try:
        ctx.obj["instance"] = resolve_instance(instance_name)
    except ValueError as e:
        if ctx.invoked_subcommand == "setup" and not list_instances():
            from src.core.instance_manager import create_instance
            ctx.obj["instance"] = create_instance("default")
        else:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


# Register commands
from src.commands.instances import instance
from src.commands.setup import setup
from src.commands.lifecycle import up, down, restart, status, reset, plan, validate
from src.commands.config import config
from src.commands.certs import cert
from src.commands.profiles import profile
from src.commands.diagnose import diagnose
from src.commands.update import update
from src.commands.backup import backup
from src.commands.volumes import volumes
from src.commands.doctor import doctor
from src.commands.support import support_bundle
from src.commands.logs import logs
from src.commands.exec import exec_cmd

main.add_command(instance)
main.add_command(setup)
main.add_command(up)
main.add_command(down)
main.add_command(restart)
main.add_command(status)
main.add_command(reset)
main.add_command(plan)
main.add_command(validate)
main.add_command(config)
main.add_command(cert)
main.add_command(profile)
main.add_command(diagnose)
main.add_command(update)
main.add_command(backup)
main.add_command(volumes)
main.add_command(doctor)
main.add_command(support_bundle)
main.add_command(logs)
main.add_command(exec_cmd)
