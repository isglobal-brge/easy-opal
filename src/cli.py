"""CLI entry point. Routes all commands and manages instance context."""

import sys

import click
from click.core import ParameterSource

from src.core.container_runtime import set_requested_runtime
from src.core.instance_manager import resolve_instance, list_instances, get_instance


# Commands that never operate on a specific instance (manage easy-opal itself).
GLOBAL_COMMANDS = {"runtime", "update"}
# Commands that run global checks and optionally use targeted instance(s).
HYBRID_COMMANDS = {"doctor"}
# The instance-management group is registered under these names.
INSTANCE_GROUP_NAMES = {"instance", "stack"}


class EasyOpalGroup(click.Group):
    """Custom group with clean exception handling."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (click.exceptions.Exit, click.exceptions.Abort, click.ClickException):
            raise
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


def _resolve_targets(instance_name, all_instances, allow_none=False):
    """Return the list of targeted instances for instance-scoped commands.

    Raises ValueError with a helpful message on ambiguity (multiple instances
    and no target) unless allow_none is set (hybrid commands run global-only).
    """
    if all_instances:
        names = list_instances()
        if not names:
            if allow_none:
                return []
            raise ValueError("No instances found. Create one with: easy-opal setup")
        return [get_instance(n) for n in names]

    if instance_name and "," in instance_name:
        names = [n.strip() for n in instance_name.split(",") if n.strip()]
        if not names:
            raise ValueError("No valid instance names provided with -i.")
        return [get_instance(n) for n in names]

    if instance_name:
        return [get_instance(instance_name)]

    # No explicit target.
    if allow_none:
        names = list_instances()
        return [get_instance(names[0])] if len(names) == 1 else []
    return [resolve_instance(None)]


def _route_setup(ctx, instance_name, all_instances):
    """setup creates or targets exactly one instance."""
    if all_instances:
        click.echo("Error: setup cannot be combined with --all.", err=True)
        sys.exit(1)
    if instance_name and "," in instance_name:
        click.echo("Error: setup targets a single instance, not a list.", err=True)
        sys.exit(1)

    ctx.obj["setup_name"] = instance_name
    if instance_name:
        try:
            ctx.obj["instance"] = get_instance(instance_name)
        except ValueError:
            # Named instance doesn't exist yet -> create it during setup.
            ctx.obj["instance"] = None
    else:
        ctx.obj["instance"] = None


@click.group(
    cls=EasyOpalGroup,
    epilog="Manage deployments with 'easy-opal instance' (alias 'stack'): "
           "list, create, info, remove. Target one with -i <name> or all with --all.",
)
@click.option("-i", "--instance", "instance_name", envvar="EASY_OPAL_INSTANCE", default=None,
              help="Target instance (auto-detected if only one exists).")
@click.option("--all", "all_instances", is_flag=True, help="Apply to all instances.")
@click.option(
    "--runtime",
    type=click.Choice(["auto", "docker", "podman"]),
    envvar="EASY_OPAL_RUNTIME",
    default="auto",
    help=(
        "Per-invocation override. Omit it to use the instance binding or saved "
        "default; explicit auto bypasses the saved default."
    ),
)
@click.pass_context
def main(ctx, instance_name, all_instances, runtime):
    """Deploy and manage OBiBa Opal environments."""
    ctx.ensure_object(dict)
    ctx.obj["all"] = all_instances
    runtime_source = ctx.get_parameter_source("runtime")
    runtime_override = (
        None if runtime_source is ParameterSource.DEFAULT else runtime
    )
    set_requested_runtime(runtime_override)

    subcommand = ctx.invoked_subcommand

    # Instance/stack management group: operates on instances by name argument.
    if subcommand in INSTANCE_GROUP_NAMES:
        return

    # Global commands never need an instance; ignore -i/--all entirely.
    if subcommand in GLOBAL_COMMANDS:
        return

    # setup creates or targets exactly one instance.
    if subcommand == "setup":
        _route_setup(ctx, instance_name, all_instances)
        return

    hybrid = subcommand in HYBRID_COMMANDS

    try:
        targets = _resolve_targets(instance_name, all_instances, allow_none=hybrid)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    ctx.obj["instances"] = targets
    ctx.obj["instance"] = targets[0] if targets else None


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
from src.commands.auto_update import auto_update
from src.commands.runtime import runtime as runtime_command

main.add_command(instance)
main.add_command(instance, name="stack")  # discoverable alias
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
main.add_command(auto_update)
main.add_command(runtime_command)
