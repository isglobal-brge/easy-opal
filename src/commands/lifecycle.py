"""Stack lifecycle commands: up, down, restart, status, reset, plan."""

import click
from rich.prompt import Confirm

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.core.auto_update_scheduler import AutoUpdateScheduleError
from src.core.container_runtime import get_runtime
from src.core.docker import compose_up, compose_down, compose_restart, compose_status, compose_reset
from src.core.host_jobs import reconcile_schedules
from src.core.instance_manager import InstanceLock
from src.utils.console import console, success, error, info, for_each_instance, require_single_instance


@click.command()
@click.pass_context
def up(ctx):
    """Start the stack (convergent — only recreates changed services)."""
    def _up(instance):
        if not config_exists(instance):
            raise click.ClickException(
                f"[{instance.name}] No configuration found. Run 'easy-opal setup' first."
            )
        config = load_config(instance)
        try:
            with InstanceLock(instance):
                info(f"Starting {instance.name}...")
                if not compose_up(instance, config):
                    raise click.ClickException(f"Failed to start {instance.name}.")
                reconcile_schedules(instance, get_runtime(instance), config)
        except AutoUpdateScheduleError as exc:
            raise click.ClickException(
                f"{instance.name} started, but scheduled jobs could not be reconciled: {exc}"
            ) from exc
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        success(f"{instance.name} is running.")
    for_each_instance(ctx, _up)


@click.command()
@click.pass_context
def down(ctx):
    """Stop the stack."""
    def _down(instance):
        if not config_exists(instance):
            raise click.ClickException(
                f"[{instance.name}] No configuration found. Run 'easy-opal setup' first."
            )
        config = load_config(instance)
        try:
            with InstanceLock(instance):
                if not compose_down(instance, config):
                    raise click.ClickException(f"Failed to stop {instance.name}.")
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        success(f"{instance.name} stopped.")
    for_each_instance(ctx, _down)


@click.command()
@click.pass_context
def restart(ctx):
    """Restart the stack (full down + up cycle)."""
    def _restart(instance):
        if not config_exists(instance):
            raise click.ClickException(
                f"[{instance.name}] No configuration found. Run 'easy-opal setup' first."
            )
        config = load_config(instance)
        try:
            with InstanceLock(instance):
                info(f"Restarting {instance.name}...")
                if not compose_restart(instance, config):
                    raise click.ClickException(f"Failed to restart {instance.name}.")
                reconcile_schedules(instance, get_runtime(instance), config)
        except AutoUpdateScheduleError as exc:
            raise click.ClickException(
                f"{instance.name} restarted, but scheduled jobs could not be reconciled: {exc}"
            ) from exc
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        success(f"{instance.name} restarted.")
    for_each_instance(ctx, _restart)


@click.command()
@click.pass_context
def status(ctx):
    """Show container status."""
    def _status(instance):
        if not config_exists(instance):
            raise click.ClickException(
                f"[{instance.name}] No configuration found. Run 'easy-opal setup' first."
            )
        config = load_config(instance)
        if not compose_status(instance, config):
            raise click.ClickException(
                f"Failed to query status for {instance.name}."
            )
    for_each_instance(ctx, _status)


@click.command()
@click.pass_context
def plan(ctx):
    """Show the generated Compose configuration without applying it."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        raise click.ClickException(
            "No configuration found. Run 'easy-opal setup' first."
        )
    config = load_config(instance)
    from src.utils.diff import show_compose_preview
    show_compose_preview(config, instance)


@click.command()
@click.pass_context
def validate(ctx):
    """Validate configuration without starting anything."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        raise click.ClickException(
            "No configuration found. Run 'easy-opal setup' first."
        )

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
        raise click.ClickException("Configuration is invalid.")
    else:
        success("Configuration is valid.")


@click.command()
@click.option("--volumes", is_flag=True, help="Also delete container volumes (data loss).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def reset(ctx, volumes, yes):
    """Stop the stack and optionally delete volumes."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        raise click.ClickException(
            "No configuration found. Run 'easy-opal setup' first."
        )

    if volumes and not yes:
        if not Confirm.ask("[bold red]This will delete ALL data. Are you sure?[/bold red]", default=False):
            return

    config = load_config(instance)
    try:
        with InstanceLock(instance):
            if volumes:
                if not compose_reset(instance, config):
                    raise click.ClickException("Failed to reset the stack.")
                success("Stack stopped and volumes deleted.")
            else:
                if not compose_down(instance, config):
                    raise click.ClickException("Failed to stop the stack.")
                success("Stack stopped.")
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
