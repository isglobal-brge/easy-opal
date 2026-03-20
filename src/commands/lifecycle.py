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
@click.pass_context
def validate(ctx):
    """Validate configuration without starting anything."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return

    config = load_config(instance)

    issues = []

    # Check hosts
    if config.ssl.strategy != "none" and not config.hosts:
        issues.append("No hosts configured (required for SSL)")

    # Check Let's Encrypt email
    if config.ssl.strategy == "letsencrypt" and not config.ssl.le_email:
        issues.append("Let's Encrypt email not set")

    # Check Mica requires Agate
    if config.mica.enabled and not config.agate.enabled:
        issues.append("Mica is enabled but Agate is not (Mica requires Agate)")

    # Check external databases have host
    for db in config.databases:
        if db.external and not db.host:
            issues.append(f"External database '{db.name}' has no host configured")

    # Check SMTP when mode is smtp
    if config.agate.enabled and config.agate.mail_mode == "smtp":
        if not config.agate.smtp.host:
            issues.append("SMTP mode selected but no SMTP host configured")

    # Try generating compose
    try:
        from src.core.docker import generate_compose
        generate_compose(config, instance)
        success("Compose file generated successfully.")
    except Exception as e:
        issues.append(f"Compose generation failed: {e}")

    if issues:
        error(f"{len(issues)} issue(s) found:")
        for issue in issues:
            console.print(f"  - {issue}")
    else:
        success("Configuration is valid.")


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
