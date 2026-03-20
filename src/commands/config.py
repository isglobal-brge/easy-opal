"""Configuration management commands."""

import json

import click
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table

from src.models.instance import InstanceContext
from src.models.enums import SSLStrategy
from src.core.config_manager import load_config, save_config, config_exists
from src.core.secrets_manager import load_secrets, save_secrets
from src.core.docker import generate_compose
from src.utils.console import console, success, error, info


@click.group()
def config():
    """Manage configuration."""
    pass


@config.command()
@click.pass_context
def show(ctx):
    """Display the current configuration."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)  # Creates default if missing
    console.print(cfg.model_dump_json(indent=2))


@config.command(name="show-version")
@click.pass_context
def show_version(ctx):
    """Show configured service versions."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return
    cfg = load_config(instance)

    table = Table(title="Service Versions")
    table.add_column("Service", style="cyan")
    table.add_column("Version", style="bold")

    table.add_row("Opal", cfg.opal_version)
    table.add_row("MongoDB", cfg.mongo_version)
    table.add_row("NGINX", cfg.nginx_version)
    for p in cfg.profiles:
        table.add_row(f"Rock ({p.name})", f"{p.image}:{p.tag}")
    for db in cfg.databases:
        table.add_row(f"{db.type.capitalize()} ({db.name})", db.version)

    console.print(table)


@config.command(name="change-version")
@click.argument("version", required=False)
@click.option("--service", default="opal", help="Service to change (opal, mongo, nginx, or a database name).")
@click.option("--pull", is_flag=True, help="Pull the new Docker image immediately.")
@click.pass_context
def change_version(ctx, version, service, pull):
    """Change a service's Docker image version."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    service_keys = {"opal": "opal_version", "mongo": "mongo_version", "nginx": "nginx_version"}

    if service in service_keys:
        current = getattr(cfg, service_keys[service])
        new = version or Prompt.ask(f"New {service} version", default=current)
        setattr(cfg, service_keys[service], new)
        save_config(cfg, instance)
        generate_compose(cfg, instance)
        success(f"{service.capitalize()} version set to {new}")

        if pull:
            from src.core.docker import pull_image
            images = {"opal": f"obiba/opal:{new}", "mongo": f"mongo:{new}", "nginx": f"nginx:{new}"}
            pull_image(images[service])
    else:
        # Try database instance
        db = next((d for d in cfg.databases if d.name == service), None)
        if not db:
            error(f"Unknown service '{service}'.")
            return
        current = db.version
        new = version or Prompt.ask(f"New {service} version", default=current)
        db.version = new
        save_config(cfg, instance)
        generate_compose(cfg, instance)
        success(f"{service} version set to {new}")

    info("Run 'easy-opal restart' to apply.")


@config.command(name="change-password")
@click.argument("password", required=False)
@click.pass_context
def change_password(ctx, password):
    """Change the Opal admin password."""
    instance: InstanceContext = ctx.obj["instance"]
    secrets = load_secrets(instance)
    new_pw = password or Prompt.ask("New admin password", password=True)
    if not new_pw or not new_pw.strip():
        error("Password cannot be empty.")
        return
    secrets["OPAL_ADMIN_PASSWORD"] = new_pw
    save_secrets(secrets, instance)
    success("Password updated. Run 'easy-opal restart' to apply.")


@config.command(name="change-port")
@click.argument("port", type=int, required=False)
@click.pass_context
def change_port(ctx, port):
    """Change the external HTTPS port."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)
    new_port = port or IntPrompt.ask("New port", default=cfg.opal_external_port)
    cfg.opal_external_port = new_port
    save_config(cfg, instance)
    generate_compose(cfg, instance)
    success(f"Port set to {new_port}. Run 'easy-opal restart' to apply.")


@config.command()
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.option("--interval", type=int, help="Poll interval in hours.")
@click.option("--cleanup/--no-cleanup", default=None)
@click.pass_context
def watchtower(ctx, action, interval, cleanup):
    """Manage Watchtower automatic container updates."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if not action and interval is None and cleanup is None:
        action = "status"

    if action == "status":
        status_str = "[green]enabled[/green]" if cfg.watchtower.enabled else "[red]disabled[/red]"
        console.print(f"Watchtower: {status_str}")
        if cfg.watchtower.enabled:
            console.print(f"  Interval: {cfg.watchtower.poll_interval_hours}h")
            console.print(f"  Cleanup:  {'yes' if cfg.watchtower.cleanup else 'no'}")
        return

    changed = False
    if action == "enable" and not cfg.watchtower.enabled:
        cfg.watchtower.enabled = True
        changed = True
        success("Watchtower enabled.")
    elif action == "disable" and cfg.watchtower.enabled:
        cfg.watchtower.enabled = False
        changed = True
        success("Watchtower disabled.")

    if interval is not None:
        cfg.watchtower.poll_interval_hours = interval
        changed = True
        success(f"Interval set to {interval}h.")

    if cleanup is not None:
        cfg.watchtower.cleanup = cleanup
        changed = True

    if changed:
        save_config(cfg, instance)
        generate_compose(cfg, instance)
        info("Run 'easy-opal restart' to apply.")
