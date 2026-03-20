"""Configuration management commands."""

import shutil

import click
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table

from src.models.config import OpalConfig, SSLConfig
from src.models.instance import InstanceContext
from src.models.enums import SSLStrategy
from src.core.config_manager import load_config, save_config, config_exists
from src.core.secrets_manager import load_secrets, save_secrets
from src.core.docker import generate_compose
from src.core.nginx import generate_nginx_config
from src.utils.console import console, success, error, info, warning


def _apply_config(cfg: OpalConfig, instance: InstanceContext, regen_certs: bool = False) -> None:
    """Save config and regenerate all derived files (compose, nginx, optionally certs)."""
    save_config(cfg, instance)

    if regen_certs and cfg.ssl.strategy == SSLStrategy.SELF_SIGNED:
        from src.core.ssl import generate_server_cert
        generate_server_cert(instance, cfg)

    if cfg.ssl.strategy != SSLStrategy.NONE:
        generate_nginx_config(cfg, instance)

    generate_compose(cfg, instance)
    info("Run 'easy-opal restart' to apply.")


@click.group()
def config():
    """Manage configuration."""
    pass


@config.command()
@click.pass_context
def show(ctx):
    """Display the current configuration."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)
    console.print(cfg.model_dump_json(indent=2))


@config.command(name="show-version")
@click.pass_context
def show_version(ctx):
    """Show configured service versions."""
    instance: InstanceContext = ctx.obj["instance"]
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
        _apply_config(cfg, instance)
        success(f"{service.capitalize()} version set to {new}")

        if pull:
            from src.core.docker import pull_image
            images = {"opal": f"obiba/opal:{new}", "mongo": f"mongo:{new}", "nginx": f"nginx:{new}"}
            pull_image(images[service])
    else:
        db = next((d for d in cfg.databases if d.name == service), None)
        if not db:
            error(f"Unknown service '{service}'.")
            return
        new = version or Prompt.ask(f"New {service} version", default=db.version)
        db.version = new
        _apply_config(cfg, instance)
        success(f"{service} version set to {new}")


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

    # Regenerate compose so the env var updates
    cfg = load_config(instance)
    generate_compose(cfg, instance)
    success("Password updated. Run 'easy-opal restart' to apply.")


@config.command(name="change-port")
@click.argument("port", type=int, required=False)
@click.pass_context
def change_port(ctx, port):
    """Change the external port. Updates CSRF automatically."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if cfg.ssl.strategy == SSLStrategy.NONE:
        new_port = port or IntPrompt.ask("New HTTP port", default=cfg.opal_http_port)
        cfg.opal_http_port = new_port
    else:
        new_port = port or IntPrompt.ask("New HTTPS port", default=cfg.opal_external_port)
        cfg.opal_external_port = new_port

    _apply_config(cfg, instance)
    success(f"Port set to {new_port}. CSRF updated.")


@config.command(name="change-hosts")
@click.argument("hosts", nargs=-1, required=False)
@click.pass_context
def change_hosts(ctx, hosts):
    """Change the host list. Regenerates certs and CSRF."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if hosts:
        new_hosts = list(hosts)
    else:
        console.print(f"Current hosts: [bold]{', '.join(cfg.hosts)}[/bold]")
        raw = Prompt.ask("New hosts (comma-separated)", default=",".join(cfg.hosts))
        new_hosts = [h.strip() for h in raw.split(",") if h.strip()]

    if not new_hosts:
        error("At least one host is required.")
        return

    cfg.hosts = new_hosts
    _apply_config(cfg, instance, regen_certs=True)
    success(f"Hosts set to: {', '.join(new_hosts)}")
    info("Certificates and CSRF updated.")


@config.command(name="change-ssl")
@click.argument("strategy", type=click.Choice(["self-signed", "letsencrypt", "manual", "none"]), required=False)
@click.option("--ssl-cert", help="Path to certificate file (for manual).")
@click.option("--ssl-key", help="Path to private key file (for manual).")
@click.option("--ssl-email", help="Let's Encrypt email.")
@click.pass_context
def change_ssl(ctx, strategy, ssl_cert, ssl_key, ssl_email):
    """Change the SSL strategy. Handles cert transitions automatically."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    old_strategy = cfg.ssl.strategy
    new_strategy = SSLStrategy(strategy) if strategy else SSLStrategy(
        Prompt.ask("New SSL strategy", choices=["self-signed", "letsencrypt", "manual", "none"], default=old_strategy)
    )

    if new_strategy == old_strategy:
        warning("Already using this strategy.")
        return

    cfg.ssl = SSLConfig(strategy=new_strategy)

    # Handle strategy-specific transitions
    if new_strategy == SSLStrategy.SELF_SIGNED:
        if not cfg.hosts:
            cfg.hosts = ["localhost", "127.0.0.1"]
        from src.core.ssl import generate_server_cert
        generate_server_cert(instance, cfg)

    elif new_strategy == SSLStrategy.MANUAL:
        cert_path = ssl_cert or Prompt.ask("Path to certificate file")
        key_path = ssl_key or Prompt.ask("Path to private key file")

        from pathlib import Path
        if not Path(cert_path).is_file() or not Path(key_path).is_file():
            error("Certificate or key file not found.")
            return
        instance.certs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(cert_path, instance.certs_dir / "opal.crt")
        shutil.copy(key_path, instance.certs_dir / "opal.key")
        success("Certificates copied.")

    elif new_strategy == SSLStrategy.LETSENCRYPT:
        cfg.ssl.le_email = ssl_email or Prompt.ask("Let's Encrypt email")
        if not cfg.hosts:
            cfg.hosts = [Prompt.ask("Domain name")]
        warning("Run 'easy-opal restart' — Let's Encrypt cert will be acquired on startup.")

    elif new_strategy == SSLStrategy.NONE:
        # Clean up NGINX config
        nginx_conf = instance.nginx_conf_dir / "nginx.conf"
        if nginx_conf.exists():
            nginx_conf.unlink()

    _apply_config(cfg, instance)
    success(f"SSL changed: {old_strategy} -> {new_strategy}")


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
        _apply_config(cfg, instance)
