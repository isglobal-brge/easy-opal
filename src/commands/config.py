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


def _apply_config(
    cfg: OpalConfig, instance: InstanceContext, regen_certs: bool = False, dry_run: bool = False
) -> None:
    """Save config and regenerate all derived files. In dry_run, show diff only."""
    if dry_run:
        old = load_config(instance)
        from src.utils.diff import show_config_diff
        console.print("[bold]Changes:[/bold]")
        show_config_diff(old, cfg)
        info("Dry run -- no changes applied.")
        return

    save_config(cfg, instance)

    if regen_certs and cfg.ssl.strategy == SSLStrategy.SELF_SIGNED:
        from src.core.ssl import generate_server_cert
        generate_server_cert(instance, cfg)

    if cfg.ssl.strategy != SSLStrategy.NONE:
        generate_nginx_config(cfg, instance)

    # Regenerate Agate config if enabled
    if cfg.agate.enabled:
        from src.core.agate_config import generate_agate_config
        secrets = load_secrets(instance)
        generate_agate_config(cfg, instance, secrets)

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


@config.command(name="show-password")
@click.pass_context
def show_password(ctx):
    """Show the current Opal admin password."""
    instance: InstanceContext = ctx.obj["instance"]
    secrets = load_secrets(instance)
    pw = secrets.get("OPAL_ADMIN_PASSWORD")
    if pw:
        console.print(f"[bold]{pw}[/bold]")
    else:
        error("No admin password found. Run setup first.")


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
@click.option("--dry-run", is_flag=True, help="Show what would change without applying.")
@click.pass_context
def change_port(ctx, port, dry_run):
    """Change the external port. Updates CSRF automatically."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if cfg.ssl.strategy == SSLStrategy.NONE:
        new_port = port or IntPrompt.ask("New HTTP port", default=cfg.opal_http_port)
        cfg.opal_http_port = new_port
    else:
        new_port = port or IntPrompt.ask("New HTTPS port", default=cfg.opal_external_port)
        cfg.opal_external_port = new_port

    _apply_config(cfg, instance, dry_run=dry_run)
    if not dry_run:
        success(f"Port set to {new_port}. CSRF updated.")


@config.command(name="remove-database")
@click.argument("name", required=False)
@click.option("--delete-volume", is_flag=True, help="Also delete the Docker volume (data loss).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def remove_database(ctx, name, delete_volume, yes):
    """Remove a database instance from the stack."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if not cfg.databases:
        error("No databases configured.")
        return

    if not name:
        for i, db in enumerate(cfg.databases):
            console.print(f"  {i}. {db.name} ({db.type}, port {db.port})")
        idx = click.prompt("Database index to remove", type=int)
        if 0 <= idx < len(cfg.databases):
            name = cfg.databases[idx].name
        else:
            error("Invalid index.")
            return

    db = next((d for d in cfg.databases if d.name == name), None)
    if not db:
        error(f"Database '{name}' not found.")
        return

    if not yes:
        msg = f"Remove database '{name}'"
        if delete_volume:
            msg += " AND delete its data volume"
        if not Confirm.ask(f"{msg}?", default=False):
            return

    cfg.databases = [d for d in cfg.databases if d.name != name]
    _apply_config(cfg, instance)
    success(f"Database '{name}' removed from config.")

    if delete_volume:
        import subprocess
        vol_name = f"{cfg.stack_name}-{name}-data"
        result = subprocess.run(
            ["docker", "volume", "rm", vol_name],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            success(f"Volume '{vol_name}' deleted.")
        else:
            warning(f"Could not delete volume '{vol_name}' (may be in use). Stop the stack first.")

    info("Run 'easy-opal restart' to apply.")


@config.command(name="change-hosts")
@click.argument("hosts", nargs=-1, required=False)
@click.option("--dry-run", is_flag=True, help="Show what would change without applying.")
@click.pass_context
def change_hosts(ctx, hosts, dry_run):
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
    _apply_config(cfg, instance, regen_certs=True, dry_run=dry_run)
    if not dry_run:
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
        cert_file = Path(cert_path)
        key_file = Path(key_path)
        if not cert_file.is_file() or not key_file.is_file():
            error("Certificate or key file not found.")
            return

        try:
            from cryptography import x509
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            x509.load_pem_x509_certificate(cert_file.read_bytes())
            load_pem_private_key(key_file.read_bytes(), password=None)
        except Exception as e:
            error(f"Invalid certificate or key: {e}")
            return

        instance.certs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(cert_path, instance.certs_dir / "opal.crt")
        shutil.copy(key_path, instance.certs_dir / "opal.key")
        success("Certificates validated and copied.")

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


@config.command()
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.option("--mail-mode", type=click.Choice(["mailpit", "smtp", "none"]), help="Email mode.")
@click.option("--smtp-host", help="SMTP server hostname.")
@click.option("--smtp-port", type=int, help="SMTP port.")
@click.option("--smtp-user", help="SMTP username.")
@click.option("--smtp-password", help="SMTP password.")
@click.option("--smtp-from", help="From email address.")
@click.option("--smtp-tls/--no-smtp-tls", default=None, help="Enable TLS.")
@click.pass_context
def agate(ctx, action, mail_mode, smtp_host, smtp_port, smtp_user, smtp_password, smtp_from, smtp_tls):
    """Manage Agate authentication server."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if not action and mail_mode is None and smtp_host is None:
        action = "status"

    if action == "status":
        status_str = "[green]enabled[/green]" if cfg.agate.enabled else "[red]disabled[/red]"
        console.print(f"Agate: {status_str}")
        if cfg.agate.enabled:
            console.print(f"  Version:   {cfg.agate.version}")
            console.print(f"  Mail mode: {cfg.agate.mail_mode}")
            if cfg.agate.mail_mode == "smtp":
                s = cfg.agate.smtp
                console.print(f"  SMTP host: {s.host}:{s.port}")
                console.print(f"  SMTP user: {s.user or '(none)'}")
                console.print(f"  SMTP from: {s.from_address}")
                console.print(f"  SMTP TLS:  {s.tls}")
            elif cfg.agate.mail_mode == "mailpit":
                console.print(f"  Mailpit:   http://localhost:{cfg.agate.mailpit_port}")
        return

    changed = False

    if action == "enable" and not cfg.agate.enabled:
        cfg.agate.enabled = True
        if cfg.agate.mail_mode == "none":
            cfg.agate.mail_mode = "mailpit"
        changed = True
        success("Agate enabled.")
    elif action == "disable" and cfg.agate.enabled:
        cfg.agate.enabled = False
        changed = True
        success("Agate disabled.")

    if mail_mode is not None:
        cfg.agate.mail_mode = mail_mode
        changed = True
        success(f"Mail mode set to: {mail_mode}")

    if smtp_host is not None:
        cfg.agate.smtp.host = smtp_host
        changed = True
    if smtp_port is not None:
        cfg.agate.smtp.port = smtp_port
        changed = True
    if smtp_user is not None:
        cfg.agate.smtp.user = smtp_user
        changed = True
    if smtp_from is not None:
        cfg.agate.smtp.from_address = smtp_from
        changed = True
    if smtp_tls is not None:
        cfg.agate.smtp.tls = smtp_tls
        cfg.agate.smtp.auth = smtp_tls  # TLS usually implies auth
        changed = True

    if smtp_password is not None:
        secrets = load_secrets(instance)
        secrets["SMTP_PASSWORD"] = smtp_password
        save_secrets(secrets, instance)
        success("SMTP password saved.")

    if changed:
        _apply_config(cfg, instance)


@config.command()
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.pass_context
def mica(ctx, action):
    """Manage Mica data portal (requires Agate)."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if not action:
        action = "status"

    if action == "status":
        status_str = "[green]enabled[/green]" if cfg.mica.enabled else "[red]disabled[/red]"
        console.print(f"Mica: {status_str}")
        if cfg.mica.enabled:
            console.print(f"  Version:         {cfg.mica.version}")
            console.print(f"  Elasticsearch:   {cfg.mica.elasticsearch_version}")
        return

    if action == "enable":
        if not cfg.agate.enabled:
            cfg.agate.enabled = True
            if cfg.agate.mail_mode == "none":
                cfg.agate.mail_mode = "mailpit"
            info("Agate auto-enabled (required by Mica).")
        cfg.mica.enabled = True
        _apply_config(cfg, instance)
        success("Mica enabled.")

    elif action == "disable":
        cfg.mica.enabled = False
        _apply_config(cfg, instance)
        success("Mica disabled.")


@config.command(name="backup")
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.option("--every", type=int, help="Backup interval in hours.")
@click.option("--keep", type=int, help="Number of backups to retain.")
@click.pass_context
def backup_config(ctx, action, every, keep):
    """Manage automated backups."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    if not action and every is None and keep is None:
        action = "status"

    if action == "status":
        status_str = "[green]enabled[/green]" if cfg.backup.enabled else "[red]disabled[/red]"
        console.print(f"Automated backup: {status_str}")
        if cfg.backup.enabled:
            console.print(f"  Interval: every {cfg.backup.interval_hours}h")
            console.print(f"  Retain:   {cfg.backup.keep} backups")

        # Show existing backups
        backups = sorted(instance.root.glob("backups/*.tar.gz"), reverse=True)
        if backups:
            console.print(f"  Backups:  {len(backups)} on disk")
            console.print(f"  Latest:   {backups[0].name}")
        return

    changed = False

    if action == "enable" and not cfg.backup.enabled:
        cfg.backup.enabled = True
        changed = True
        success("Automated backup enabled.")
    elif action == "disable" and cfg.backup.enabled:
        cfg.backup.enabled = False
        changed = True
        success("Automated backup disabled.")

    if every is not None:
        cfg.backup.interval_hours = every
        changed = True
        success(f"Backup interval set to {every}h.")

    if keep is not None:
        cfg.backup.keep = keep
        changed = True
        success(f"Retaining {keep} backups.")

    if changed:
        _apply_config(cfg, instance)
