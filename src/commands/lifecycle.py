"""Stack lifecycle commands: up, down, restart, status, reset, plan."""

import click
from rich.prompt import Confirm

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.core.docker import compose_up, compose_down, compose_restart, compose_status, compose_reset, check_docker
from src.utils.console import console, success, error, info


@click.command()
@click.pass_context
def up(ctx):
    """Start the stack (convergent — only recreates changed services)."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return
    if not check_docker():
        return
    config = load_config(instance)
    info("Starting stack...")
    if compose_up(instance, config):
        success("Stack is running.")


@click.command()
@click.pass_context
def down(ctx):
    """Stop the stack."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return
    config = load_config(instance)
    compose_down(instance, config)
    success("Stack stopped.")


@click.command()
@click.pass_context
def restart(ctx):
    """Restart the stack (full down + up cycle)."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return
    config = load_config(instance)
    info("Restarting stack...")
    if compose_restart(instance, config):
        success("Stack restarted.")


@click.command()
@click.pass_context
def status(ctx):
    """Show container status."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return
    config = load_config(instance)
    compose_status(instance, config)


@click.command()
@click.pass_context
def plan(ctx):
    """Show what docker-compose.yml would look like without applying."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return
    config = load_config(instance)
    from src.utils.diff import show_compose_preview
    show_compose_preview(config, instance)


@click.command()
@click.option("--volumes", is_flag=True, help="Also delete Docker volumes (data loss).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def reset(ctx, volumes, yes):
    """Stop the stack and optionally delete volumes."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    if volumes and not yes:
        if not Confirm.ask("[bold red]This will delete ALL data. Are you sure?[/bold red]", default=False):
            return

    config = load_config(instance)
    if volumes:
        compose_reset(instance, config)
        success("Stack stopped and volumes deleted.")
    else:
        compose_down(instance, config)
        success("Stack stopped.")
