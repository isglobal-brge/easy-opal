"""Configuration management commands."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import click
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.table import Table

from src.models.config import OpalConfig, SSLConfig
from src.models.instance import InstanceContext
from src.models.enums import SSLStrategy
from src.core.config_manager import ensure_config_unchanged, load_config, save_config
from src.core.instance_manager import InstanceLock
from src.core.container_runtime import (
    get_runtime,
    list_project_volumes,
    validate_runtime_config,
)
from src.core.secrets_manager import load_secrets, save_secrets
from src.core.docker import (
    generate_compose,
    obtain_letsencrypt_certificate,
    restore_running_nginx,
)
from src.core.auto_update_scheduler import AutoUpdateScheduleError
from src.core.host_jobs import preflight_enabled_schedules, reconcile_schedules
from src.core.nginx import generate_nginx_config
from src.utils.console import console, success, error, info, warning, require_single_instance


def _config_artifact_paths(instance: InstanceContext) -> tuple[Path, ...]:
    """Files that one configuration apply may create, replace, or remove."""
    return (
        instance.config_path,
        instance.secrets_path,
        instance.compose_path,
        instance.nginx_conf_dir / "nginx.conf",
        instance.nginx_html_dir / "maintenance.html",
        instance.data_dir / "agate" / "conf" / "application-prod.yml",
        instance.data_dir / "armadillo-config" / "application.yml",
        instance.certs_dir / "ca.key",
        instance.certs_dir / "ca.crt",
        instance.certs_dir / "opal.key",
        instance.certs_dir / "opal.crt",
    )


def _snapshot_config_artifacts(
    instance: InstanceContext,
) -> dict[Path, tuple[bytes, int] | None]:
    snapshots: dict[Path, tuple[bytes, int] | None] = {}
    for path in _config_artifact_paths(instance):
        if path.is_symlink():
            raise click.ClickException(
                f"Refusing to update configuration through symlink: {path}"
            )
        if not path.exists():
            snapshots[path] = None
            continue
        if not path.is_file():
            raise click.ClickException(
                f"Expected configuration artifact to be a file: {path}"
            )
        snapshots[path] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return snapshots


def _restore_config_artifacts(
    snapshots: dict[Path, tuple[bytes, int] | None],
) -> None:
    for path, snapshot in snapshots.items():
        if snapshot is None:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                raise OSError(f"Cannot remove non-file artifact created at {path}")
            continue

        content, mode = snapshot
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent
        )
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            Path(temporary).unlink(missing_ok=True)


def _validate_manual_certificate_pair(
    cert_path: str | Path, key_path: str | Path
) -> tuple[Path, Path]:
    """Validate a PEM certificate/private-key pair without changing instance files."""
    cert_file = Path(cert_path)
    key_file = Path(key_path)
    if not cert_file.is_file() or not key_file.is_file():
        raise click.ClickException("Certificate or key file not found.")

    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        certificate = x509.load_pem_x509_certificate(cert_file.read_bytes())
        private_key = serialization.load_pem_private_key(
            key_file.read_bytes(), password=None
        )
        certificate_key = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_public_key = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if certificate_key != private_public_key:
            raise ValueError("certificate and private key do not match")
    except Exception as exc:
        raise click.ClickException(f"Invalid certificate or key: {exc}") from exc

    return cert_file, key_file


def _apply_config(
    cfg: OpalConfig,
    instance: InstanceContext,
    regen_certs: bool = False,
    dry_run: bool = False,
) -> None:
    """Apply one configuration change while excluding lifecycle/update jobs."""
    if dry_run:
        _apply_config_locked(cfg, instance, regen_certs, dry_run=True)
        return
    try:
        with InstanceLock(instance):
            _apply_config_locked(cfg, instance, regen_certs)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _apply_config_locked(
    cfg: OpalConfig,
    instance: InstanceContext,
    regen_certs: bool = False,
    dry_run: bool = False,
    manual_certificates: tuple[Path, Path] | None = None,
    secret_updates: dict[str, str] | None = None,
    allow_stale: bool = False,
) -> None:
    """Save config and regenerate all derived files. In dry_run, show diff only."""
    if dry_run:
        old = load_config(instance)
        from src.utils.diff import show_config_diff
        console.print("[bold]Changes:[/bold]")
        show_config_diff(old, cfg)
        info("Dry run -- no changes applied.")
        return

    if not allow_stale:
        try:
            ensure_config_unchanged(cfg, instance)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    runtime = get_runtime(instance)
    validate_runtime_config(runtime, cfg)
    from src.services import ServiceRegistry

    ServiceRegistry(
        cfg, instance, {}, runtime_name=runtime.name
    ).validate_runtime_support()

    previous = load_config(instance)
    try:
        preflight_enabled_schedules(instance, runtime, cfg)
    except AutoUpdateScheduleError as exc:
        raise click.ClickException(
            f"Scheduled job preflight failed; configuration was not changed: {exc}"
        ) from exc

    snapshots = _snapshot_config_artifacts(instance)
    schedule_reconcile_started = False
    try:
        if secret_updates:
            secrets = load_secrets(instance)
            secrets.update(secret_updates)
            save_secrets(secrets, instance)

        if regen_certs and cfg.ssl.strategy == SSLStrategy.SELF_SIGNED:
            from src.core.ssl import generate_server_cert
            generate_server_cert(instance, cfg)
        elif manual_certificates is not None:
            cert_file, key_file = manual_certificates
            instance.certs_dir.mkdir(parents=True, exist_ok=True)
            destinations = (
                (cert_file, instance.certs_dir / "opal.crt"),
                (key_file, instance.certs_dir / "opal.key"),
            )
            for source, destination in destinations:
                if source.resolve() != destination.resolve():
                    shutil.copy2(source, destination)
            (instance.certs_dir / "opal.crt").chmod(0o644)
            (instance.certs_dir / "opal.key").chmod(0o600)

        generate_nginx_config(cfg, instance)

        if cfg.agate.enabled:
            from src.core.agate_config import generate_agate_config
            secrets = load_secrets(instance)
            generate_agate_config(cfg, instance, secrets)

        generate_compose(cfg, instance)

        schedule_reconcile_started = True
        reconcile_schedules(instance, runtime, cfg)
        # Commit the source of truth last. Scheduled jobs take the same instance
        # lock, so none can observe the short interval between schedule install
        # and this atomic logical commit.
        save_config(cfg, instance)
    except Exception as exc:
        rollback_errors: list[str] = []
        if schedule_reconcile_started:
            try:
                reconcile_schedules(instance, runtime, previous)
            except Exception as rollback_exc:
                rollback_errors.append(f"schedules: {rollback_exc}")
        try:
            _restore_config_artifacts(snapshots)
        except Exception as rollback_exc:
            rollback_errors.append(f"files: {rollback_exc}")

        if rollback_errors:
            raise click.ClickException(
                "Configuration update failed and rollback was incomplete: "
                f"{exc}; {'; '.join(rollback_errors)}"
            ) from exc
        if isinstance(exc, click.ClickException):
            raise
        raise click.ClickException(
            f"Configuration update failed; previous files and schedules restored: {exc}"
        ) from exc
    info("Run 'easy-opal restart' to apply.")


@click.group()
def config():
    """Manage configuration."""
    pass


@config.command()
@click.pass_context
def show(ctx):
    """Display the current configuration."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)
    console.print(cfg.model_dump_json(indent=2))


@config.command(name="show-version")
@click.pass_context
def show_version(ctx):
    """Show configured service versions."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    table = Table(title="Service Versions")
    table.add_column("Service", style="cyan")
    table.add_column("Version", style="bold")

    if cfg.flavor == "opal":
        table.add_row("Opal", cfg.opal_version)
        table.add_row("MongoDB", cfg.mongo_version)
    else:
        table.add_row("Armadillo", cfg.armadillo.version)
    table.add_row("NGINX", cfg.nginx_version)
    for p in cfg.profiles:
        table.add_row(f"Rock ({p.name})", f"{p.image}:{p.tag}")
    for db in cfg.databases:
        table.add_row(f"{db.type.capitalize()} ({db.name})", db.version)

    console.print(table)


@config.command(name="change-version")
@click.argument("version", required=False)
@click.option("--service", default="opal", help="Service to change (opal, mongo, nginx, or a database name).")
@click.option("--pull", is_flag=True, help="Pull the new container image immediately.")
@click.pass_context
def change_version(ctx, version, service, pull):
    """Change a service's container image version."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    service_keys = {"opal": "opal_version", "mongo": "mongo_version", "nginx": "nginx_version"}

    # Auto-detect: if user says "opal" but flavor is armadillo, map to armadillo
    if service == "opal" and cfg.flavor == "armadillo":
        service = "armadillo"

    if service == "armadillo":
        current = cfg.armadillo.version
        new = version or Prompt.ask("New Armadillo version", default=current)
        cfg.armadillo.version = new
        _apply_config(cfg, instance)
        success(f"Armadillo version set to {new}")

        if pull:
            result = get_runtime(instance).pull(
                f"docker.io/molgenis/molgenis-armadillo:{new}"
            )
            if result.returncode != 0:
                raise click.ClickException(
                    "Configuration was updated, but the Armadillo image pull failed."
                )
    elif service in service_keys:
        current = getattr(cfg, service_keys[service])
        new = version or Prompt.ask(f"New {service} version", default=current)
        setattr(cfg, service_keys[service], new)
        _apply_config(cfg, instance)
        success(f"{service.capitalize()} version set to {new}")

        if pull:
            images = {
                "opal": f"docker.io/obiba/opal:{new}",
                "mongo": f"docker.io/library/mongo:{new}",
                "nginx": f"docker.io/library/nginx:{new}",
            }
            result = get_runtime(instance).pull(images[service])
            if result.returncode != 0:
                raise click.ClickException(
                    f"Configuration was updated, but the {service} image pull failed."
                )
    else:
        db = next((d for d in cfg.databases if d.name == service), None)
        if not db:
            error(f"Unknown service '{service}'.")
            return
        new = version or Prompt.ask(f"New {service} version", default=db.version)
        db.version = new
        _apply_config(cfg, instance)
        success(f"{service} version set to {new}")


def _admin_pw_key(instance: InstanceContext) -> str:
    """Get the correct password key based on flavor."""
    from src.core.config_manager import config_exists, load_config
    if config_exists(instance):
        cfg = load_config(instance)
        if cfg.flavor == "armadillo":
            return "ARMADILLO_ADMIN_PASSWORD"
    return "OPAL_ADMIN_PASSWORD"


@config.command(name="show-password")
@click.pass_context
def show_password(ctx):
    """Show the current admin password."""
    instance: InstanceContext = require_single_instance(ctx)
    secrets = load_secrets(instance)
    key = _admin_pw_key(instance)
    pw = secrets.get(key)
    if pw:
        console.print(f"[bold]{pw}[/bold]")
    else:
        error("No admin password found. Run setup first.")


@config.command(name="change-password")
@click.argument("password", required=False)
@click.pass_context
def change_password(ctx, password):
    """Change the admin password."""
    instance: InstanceContext = require_single_instance(ctx)
    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    new_pw = password or Prompt.ask("New admin password", password=True)
    if not new_pw or not new_pw.strip():
        error("Password cannot be empty.")
        return
    cfg = load_config(instance)
    key = (
        "ARMADILLO_ADMIN_PASSWORD"
        if cfg.flavor == "armadillo"
        else "OPAL_ADMIN_PASSWORD"
    )
    _apply_config_locked(cfg, instance, secret_updates={key: new_pw})
    success("Password updated. Run 'easy-opal restart' to apply.")


@config.command(name="change-port")
@click.argument("port", type=int, required=False)
@click.option("--dry-run", is_flag=True, help="Show what would change without applying.")
@click.pass_context
def change_port(ctx, port, dry_run):
    """Change the external port and regenerate proxy settings."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    if cfg.ssl.strategy == SSLStrategy.NONE:
        new_port = port or IntPrompt.ask("New HTTP port", default=cfg.opal_http_port)
        cfg.opal_http_port = new_port
    else:
        new_port = port or IntPrompt.ask("New HTTPS port", default=cfg.opal_external_port)
        cfg.opal_external_port = new_port

    _apply_config(cfg, instance, dry_run=dry_run)
    if not dry_run:
        success(f"Port set to {new_port}. Proxy settings updated.")


@config.command(name="remove-database")
@click.argument("name", required=False)
@click.option("--delete-volume", is_flag=True, help="Also delete the data volume (data loss).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def remove_database(ctx, name, delete_volume, yes):
    """Remove a database instance from the stack."""
    instance: InstanceContext = require_single_instance(ctx)
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

    if delete_volume:
        logical_name = f"{cfg.stack_name}-{name}-data"
        runtime = get_runtime(instance)
        try:
            project_volumes = list_project_volumes(runtime, cfg.stack_name)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.output or "").strip()
            suffix = f": {detail}" if detail else ""
            raise click.ClickException(
                "Could not list project volumes "
                f"(exit code {exc.returncode}){suffix}"
            ) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise click.ClickException(
                f"Could not list project volumes: {exc}"
            ) from exc

        matches = [
            volume
            for volume in project_volumes
            if volume == logical_name or volume.endswith(f"_{logical_name}")
        ]
        if len(matches) != 1:
            raise click.ClickException(
                f"Could not identify one physical volume for '{logical_name}'; "
                "no volume was removed."
            )
        physical_volume = matches[0]

    cfg.databases = [d for d in cfg.databases if d.name != name]
    _apply_config(cfg, instance)
    success(f"Database '{name}' removed from config.")

    if delete_volume:
        try:
            result = runtime.run(
                ["volume", "rm", physical_volume],
                capture_output=True, text=True, check=False,
            )
        except OSError as exc:
            raise click.ClickException(
                f"Could not delete volume '{physical_volume}' after removing "
                f"database '{name}' from config: {exc}"
            ) from exc
        if result.returncode == 0:
            success(f"Volume '{physical_volume}' deleted.")
        else:
            detail = (result.stderr or result.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            raise click.ClickException(
                f"Could not delete volume '{physical_volume}' after removing "
                f"database '{name}' from config (exit code {result.returncode})"
                f"{suffix}"
            )

    info("Run 'easy-opal restart' to apply.")


@config.command(name="change-hosts")
@click.argument("hosts", nargs=-1, required=False)
@click.option("--dry-run", is_flag=True, help="Show what would change without applying.")
@click.pass_context
def change_hosts(ctx, hosts, dry_run):
    """Change the host list. Regenerates certificates and proxy settings."""
    instance: InstanceContext = require_single_instance(ctx)
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
        info("Certificates and proxy settings updated.")


@config.command(name="change-ssl")
@click.argument("strategy", type=click.Choice(["self-signed", "letsencrypt", "manual", "none"]), required=False)
@click.option("--ssl-cert", help="Path to certificate file (for manual).")
@click.option("--ssl-key", help="Path to private key file (for manual).")
@click.option("--ssl-email", help="Let's Encrypt email.")
@click.pass_context
def change_ssl(ctx, strategy, ssl_cert, ssl_key, ssl_email):
    """Change the SSL strategy. Handles cert transitions automatically."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    old_strategy = cfg.ssl.strategy
    previous_config = cfg.model_copy(deep=True)
    new_strategy = SSLStrategy(strategy) if strategy else SSLStrategy(
        Prompt.ask("New SSL strategy", choices=["self-signed", "letsencrypt", "manual", "none"], default=old_strategy)
    )

    if new_strategy == old_strategy:
        warning("Already using this strategy.")
        return

    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    cfg.ssl = SSLConfig(strategy=new_strategy)
    regen_certs = False
    manual_certificates: tuple[Path, Path] | None = None

    # Handle strategy-specific transitions
    if new_strategy == SSLStrategy.SELF_SIGNED:
        if not cfg.hosts:
            cfg.hosts = ["localhost", "127.0.0.1"]
        regen_certs = True

    elif new_strategy == SSLStrategy.MANUAL:
        cert_path = ssl_cert or Prompt.ask("Path to certificate file")
        key_path = ssl_key or Prompt.ask("Path to private key file")
        manual_certificates = _validate_manual_certificate_pair(
            cert_path, key_path
        )

    elif new_strategy == SSLStrategy.LETSENCRYPT:
        cfg.ssl.le_email = ssl_email or Prompt.ask("Let's Encrypt email")
        if not cfg.hosts:
            cfg.hosts = [Prompt.ask("Domain name")]

    _apply_config_locked(
        cfg,
        instance,
        regen_certs=regen_certs,
        manual_certificates=manual_certificates,
    )
    if new_strategy == SSLStrategy.LETSENCRYPT:
        info("Requesting Let's Encrypt certificate...")
        acquisition_error: Exception | None = None
        nginx_was_running = False
        try:
            acquisition = obtain_letsencrypt_certificate(cfg, instance)
            acquired = bool(acquisition)
            nginx_was_running = bool(
                getattr(acquisition, "nginx_was_running", False)
            )
        except Exception as exc:
            acquired = False
            acquisition_error = exc
            nginx_was_running = bool(
                getattr(exc, "nginx_was_running", False)
            )
        if not acquired:
            try:
                _apply_config_locked(
                    previous_config, instance, allow_stale=True
                )
            except Exception as rollback_exc:
                raise click.ClickException(
                    "Let's Encrypt acquisition failed and the previous SSL "
                    f"configuration could not be restored: {rollback_exc}"
                ) from acquisition_error or rollback_exc
            if nginx_was_running and not restore_running_nginx(
                previous_config, instance
            ):
                raise click.ClickException(
                    "Let's Encrypt acquisition failed; the previous SSL files "
                    "were restored, but the previously running NGINX service "
                    "could not be restarted."
                ) from acquisition_error
            detail = f": {acquisition_error}" if acquisition_error else ""
            raise click.ClickException(
                "Let's Encrypt acquisition failed; previous SSL configuration "
                f"restored{detail}"
            ) from acquisition_error
        success("Let's Encrypt certificate obtained.")
    if manual_certificates is not None:
        success("Certificates validated and copied.")
    success(f"SSL changed: {old_strategy} -> {new_strategy}")


@config.command()
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.option("--interval", type=click.IntRange(min=1), help="Poll interval in hours.")
@click.option("--cleanup/--no-cleanup", default=None)
@click.pass_context
def watchtower(ctx, action, interval, cleanup):
    """Manage runtime-neutral automatic updates (legacy command name)."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    if not action and interval is None and cleanup is None:
        action = "status"

    if action == "status":
        status_str = "[green]enabled[/green]" if cfg.watchtower.enabled else "[red]disabled[/red]"
        console.print(f"Automatic updates: {status_str}")
        if cfg.watchtower.enabled:
            console.print(f"  Interval: {cfg.watchtower.poll_interval_hours}h")
            console.print(f"  Cleanup:  {'yes' if cfg.watchtower.cleanup else 'no'}")
        try:
            from src.core.auto_update_scheduler import auto_update_schedule_status

            schedule = auto_update_schedule_status(instance)
            state = "active" if schedule.enabled and schedule.active else "inactive"
            console.print(f"  Scheduler: {schedule.backend} ({state})")
        except AutoUpdateScheduleError as exc:
            warning(f"  Scheduler status unavailable: {exc}")
        return

    changed = False
    if action == "enable":
        cfg.watchtower.enabled = True
        changed = True
    elif action == "disable":
        cfg.watchtower.enabled = False
        changed = True

    if interval is not None:
        cfg.watchtower.poll_interval_hours = interval
        changed = True
        success(f"Interval set to {interval}h.")

    if cleanup is not None:
        cfg.watchtower.cleanup = cleanup
        changed = True

    if changed:
        _apply_config(cfg, instance)
        if cfg.watchtower.enabled:
            success("Automatic updates enabled.")
        else:
            success("Automatic updates disabled.")


# Neutral name for new installations; retain `watchtower` as a schema and CLI
# compatibility alias for existing automation.
config.add_command(watchtower, name="auto-updates")


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
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    if not action and all(
        option is None
        for option in (
            mail_mode,
            smtp_host,
            smtp_port,
            smtp_user,
            smtp_password,
            smtp_from,
            smtp_tls,
        )
    ):
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

    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

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

    secret_updates = None
    if smtp_password is not None:
        secret_updates = {"SMTP_PASSWORD": smtp_password}
        changed = True

    if changed:
        _apply_config_locked(
            cfg, instance, secret_updates=secret_updates
        )
        if secret_updates:
            success("SMTP password saved.")


@config.command()
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.pass_context
def mica(ctx, action):
    """Manage Mica data portal (requires Agate)."""
    instance: InstanceContext = require_single_instance(ctx)
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


@config.command(name="profile-updates")
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.option("--every", type=click.IntRange(min=1), help="Pre-pull interval in hours.")
@click.pass_context
def profile_updates_config(ctx, action, every):
    """Manage scheduled pre-pulling of profile images (applied on next restart)."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    if not action and every is None:
        action = "status"

    if action == "status":
        pu = cfg.profile_updater
        status_str = "[green]enabled[/green]" if pu.enabled else "[red]disabled[/red]"
        console.print(f"Profile updates: {status_str}")
        if pu.enabled:
            console.print(f"  Interval: every {pu.interval_hours}h")
            console.print(f"  Profiles: {', '.join(p.name for p in cfg.profiles)}")
            console.print("  [dim]New images are pre-pulled by a host timer.[/dim]")
            console.print("  [dim]Run 'easy-opal restart' to apply.[/dim]")
        try:
            from src.core.auto_update_scheduler import profile_update_schedule_status

            schedule = profile_update_schedule_status(instance)
            state = "active" if schedule.enabled and schedule.active else "inactive"
            console.print(f"  Scheduler: {schedule.backend} ({state})")
        except AutoUpdateScheduleError as exc:
            warning(f"  Scheduler status unavailable: {exc}")
        return

    changed = False

    if action == "enable":
        cfg.profile_updater.enabled = True
        changed = True
    elif action == "disable":
        cfg.profile_updater.enabled = False
        changed = True

    if every is not None:
        cfg.profile_updater.interval_hours = every
        changed = True
        success(f"Pre-pull interval set to {every}h.")

    if changed:
        _apply_config(cfg, instance)
        if cfg.profile_updater.enabled:
            success("Profile updates enabled.")
            info("Images will be pre-pulled by a host timer. Run 'easy-opal restart' to apply.")
        else:
            success("Profile updates disabled.")


@config.command(name="backup")
@click.argument("action", type=click.Choice(["enable", "disable", "status"]), required=False)
@click.option("--every", type=click.IntRange(min=1), help="Backup interval in hours.")
@click.option("--keep", type=click.IntRange(min=0), help="Number of backups to retain.")
@click.pass_context
def backup_config(ctx, action, every, keep):
    """Manage automated backups."""
    instance: InstanceContext = require_single_instance(ctx)
    cfg = load_config(instance)

    if not action and every is None and keep is None:
        action = "status"

    if action == "status":
        status_str = "[green]enabled[/green]" if cfg.backup.enabled else "[red]disabled[/red]"
        console.print(f"Automated backup: {status_str}")
        if cfg.backup.enabled:
            console.print(f"  Interval: every {cfg.backup.interval_hours}h")
            retain = f"{cfg.backup.keep} backups" if cfg.backup.keep > 0 else "[yellow]no limit[/yellow]"
            console.print(f"  Retain:   {retain}")

        # Show existing backups
        backups = sorted(instance.root.glob("backups/*.tar.gz"), reverse=True)
        if backups:
            console.print(f"  Backups:  {len(backups)} on disk")
            console.print(f"  Latest:   {backups[0].name}")
        try:
            from src.core.auto_update_scheduler import backup_schedule_status

            schedule = backup_schedule_status(instance)
            state = "active" if schedule.enabled and schedule.active else "inactive"
            console.print(f"  Scheduler: {schedule.backend} ({state})")
        except AutoUpdateScheduleError as exc:
            warning(f"  Scheduler status unavailable: {exc}")
        return

    changed = False

    if action == "enable":
        cfg.backup.enabled = True
        changed = True
    elif action == "disable":
        cfg.backup.enabled = False
        changed = True

    if every is not None:
        cfg.backup.interval_hours = every
        changed = True
        success(f"Backup interval set to {every}h.")

    if keep is not None:
        cfg.backup.keep = keep
        changed = True
        if keep == 0:
            warning("No limit set. Backups will accumulate and may fill up disk space.")
        else:
            success(f"Retaining {keep} backups.")

    if changed:
        _apply_config(cfg, instance)
        if cfg.backup.enabled:
            success("Automated backup enabled.")
        else:
            success("Automated backup disabled.")
