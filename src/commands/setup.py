"""Interactive and non-interactive setup wizard."""

import shutil
from pathlib import Path

import click
from rich.prompt import Prompt, IntPrompt, Confirm

from src.models import OpalConfig, SSLConfig, DatabaseConfig, SSLStrategy, DatabaseType
from src.models.instance import InstanceContext
from src.core.config_manager import save_config, load_config, config_exists
from src.core.secrets_manager import ensure_secrets
from src.core.ssl import generate_server_cert
from src.core.nginx import generate_nginx_config
from src.core.container_runtime import (
    RuntimeSelectionError,
    get_runtime,
    get_runtime_selection,
    probe_runtimes,
    rootless_port_threshold,
    set_requested_runtime,
    validate_runtime_config,
)
from src.core.docker import (
    compose_up,
    generate_compose,
    obtain_letsencrypt_certificate,
    restore_running_nginx,
)
from src.core.auto_update_scheduler import AutoUpdateScheduleError
from src.core.host_jobs import (
    preflight_enabled_schedules,
    reconcile_schedules,
)
from src.core.instance_manager import InstanceLock
from src.commands.config import (
    _restore_config_artifacts,
    _snapshot_config_artifacts,
    _validate_manual_certificate_pair,
)
from src.utils.console import console, display_header, success, error, info, dim, warning
from src.utils.network import find_free_port, get_local_ip, validate_port


def _collect_general(config: OpalConfig) -> OpalConfig:
    """Step 1: Flavor and service versions."""
    info("1. General Configuration")
    config.flavor = Prompt.ask("Deployment type", choices=["opal", "armadillo"], default=config.flavor)

    dim("All services default to 'latest'. Press Enter to accept.")
    if config.flavor == "opal":
        config.opal_version = Prompt.ask("  Opal version", default=config.opal_version)
        config.mongo_version = Prompt.ask("  MongoDB version", default=config.mongo_version)
    else:
        config.armadillo.version = Prompt.ask("  Armadillo version", default=config.armadillo.version)
    return config


def _collect_ssl(config: OpalConfig) -> OpalConfig:
    """Step 2: SSL strategy and related config."""
    info("\n2. SSL Configuration")
    strategy = Prompt.ask(
        "SSL strategy",
        choices=["self-signed", "letsencrypt", "manual", "none"],
        default=config.ssl.strategy,
    )
    config.ssl = SSLConfig(strategy=SSLStrategy(strategy))

    if strategy == "none":
        while True:
            port = IntPrompt.ask("HTTP port to expose Opal on", default=config.opal_http_port)
            if err := validate_port(port):
                error(err)
                continue
            break
        config.opal_http_port = port
        config.hosts = []
    else:
        while True:
            port = IntPrompt.ask("External HTTPS port", default=config.opal_external_port)
            if err := validate_port(port):
                error(err)
                continue
            break
        config.opal_external_port = port

        if strategy == "self-signed":
            hosts = ["localhost", "127.0.0.1"]
            local_ip = get_local_ip()
            if local_ip not in hosts:
                hosts.append(local_ip)
            console.print(f"  Default hosts: [green]{', '.join(hosts)}[/green]")
            while Confirm.ask("  Add another host?", default=False):
                host = Prompt.ask("  Hostname or IP")
                if host and host not in hosts:
                    hosts.append(host)
            config.hosts = hosts

        elif strategy == "letsencrypt":
            config.ssl.le_email = Prompt.ask("Let's Encrypt email")
            domain = Prompt.ask("Domain name (e.g., opal.example.com)")
            config.hosts = [domain]

        elif strategy == "manual":
            host = Prompt.ask("Primary hostname for this certificate")
            config.hosts = [host]

    return config


def _collect_databases(config: OpalConfig) -> OpalConfig:
    """Step 3: Additional databases."""
    info("\n3. Database Configuration")
    dim("MongoDB is always included as Opal's metadata store.")

    if not Confirm.ask("Deploy additional database containers?", default=False):
        return config

    used_ports: list[int] = []
    defaults = {"postgres": 5432, "mysql": 3306, "mariadb": 3307}

    while True:
        db_type = Prompt.ask(
            "  Database type", choices=["postgres", "mysql", "mariadb", "done"], default="done"
        )
        if db_type == "done":
            break

        name = Prompt.ask("  Instance name", default=db_type)
        while True:
            port = IntPrompt.ask("  Port", default=find_free_port(defaults[db_type], used_ports))
            port_err = validate_port(port)
            if port_err:
                error(f"  {port_err}")
                continue
            break
        version = Prompt.ask("  Version", default="latest")
        user = Prompt.ask("  Username", default="opal")

        config.databases.append(
            DatabaseConfig(type=DatabaseType(db_type), name=name, port=port, version=version, user=user)
        )
        used_ports.append(port)
        success(f"  Added {name} ({db_type}) on port {port}")

    return config


def _collect_watchtower(config: OpalConfig, runtime_name: str) -> OpalConfig:
    """Step 4: runtime-neutral automatic updates."""
    info("\n4. Automatic Updates")
    dim(
        f"Easy-Opal will use a host timer and {runtime_name}; no engine socket "
        "is exposed to another container."
    )

    config.watchtower.enabled = Confirm.ask("Enable automatic updates?", default=False)
    if config.watchtower.enabled:
        while True:
            interval = IntPrompt.ask(
                "  Check every (hours)",
                default=config.watchtower.poll_interval_hours,
            )
            if interval >= 1:
                config.watchtower.poll_interval_hours = interval
                break
            error("  Interval must be at least one hour.")
        config.watchtower.cleanup = Confirm.ask("  Remove old images after updates?", default=True)

    return config


def _collect_backup(config: OpalConfig, runtime_name: str) -> OpalConfig:
    """Automated backups."""
    info("\nAutomated Backups")
    dim(f"A host timer creates backups through {runtime_name}.")

    config.backup.enabled = Confirm.ask("Enable automated backups?", default=False)
    if config.backup.enabled:
        while True:
            interval = IntPrompt.ask(
                "  Backup every (hours)", default=config.backup.interval_hours
            )
            if interval >= 1:
                config.backup.interval_hours = interval
                break
            error("  Interval must be at least one hour.")
        if Confirm.ask("  Limit number of backups? (recommended)", default=True):
            while True:
                keep = IntPrompt.ask(
                    "  Keep how many?", default=config.backup.keep
                )
                if keep >= 0:
                    config.backup.keep = keep
                    break
                error("  Number of backups cannot be negative.")
        else:
            config.backup.keep = 0
            warning("  No limit set. Backups will accumulate and may fill up disk space.")

    return config


def _collect_optional_services(config: OpalConfig) -> OpalConfig:
    """Step 5: Optional services."""
    info("\n5. Optional Services")

    if config.flavor == "opal":
        if Confirm.ask("Enable Agate (authentication server)?", default=False):
            config.agate.enabled = True
            config.agate.version = Prompt.ask("  Agate version", default=config.agate.version)
            mail = Prompt.ask("  Email mode", choices=["mailpit", "smtp", "none"], default="mailpit")
            config.agate.mail_mode = mail

            if Confirm.ask("  Enable Mica (data portal)? Requires Agate + Elasticsearch", default=False):
                config.mica.enabled = True
                config.mica.version = Prompt.ask("  Mica version", default=config.mica.version)

    elif config.flavor == "armadillo":
        if Confirm.ask("Enable Keycloak (OIDC authentication)?", default=False):
            config.keycloak.enabled = True
            config.armadillo.auth_mode = "oidc"

    return config


def _manual_certificate_sources(
    *,
    interactive: bool,
    cert_path: str | None,
    key_path: str | None,
) -> tuple[Path, Path]:
    """Collect and validate a manual certificate/key pair without mutations."""
    if interactive:
        cert_path = Prompt.ask("Path to your SSL certificate file (.crt/.pem)")
        key_path = Prompt.ask("Path to your SSL private key file (.key)")

    return _validate_manual_certificate_pair(cert_path or "", key_path or "")


def _resolve_setup_runtime(
    instance: InstanceContext | None, *, interactive: bool
):
    """Resolve runtime, prompting only for an unconfigured unbound target."""
    choice, explicit = get_runtime_selection()
    if not interactive or choice != "auto" or explicit:
        return get_runtime(instance, selection=(choice, explicit))

    if instance is not None:
        from src.core.instance_manager import get_instance_runtime

        if get_instance_runtime(instance) or instance.config_path.exists():
            return get_runtime(instance, selection=(choice, explicit))

    available, failures = probe_runtimes()
    if not available:
        detail = "; ".join(
            f"{name}: {failure}" for name, failure in failures.items()
        )
        raise RuntimeSelectionError(
            "No usable container runtime with Compose was found. " + detail
        )

    choices = [name for name in ("docker", "podman") if name in available]
    if len(choices) == 1:
        selected = choices[0]
        info(f"Using the only available container runtime: {selected}.")
    else:
        info("Both Docker and Podman are usable.")
        selected = Prompt.ask(
            "Container runtime for this instance",
            choices=choices,
            default="docker",
        )

    set_requested_runtime(selected)
    return available[selected]


@click.command()
@click.option("--name", help="Instance name (also used as the Compose stack name).")
@click.option("--stack-name", help="Alias of --name (kept for compatibility).")
@click.option("--host", "hosts", multiple=True, help="Hostname or IP (repeatable).")
@click.option("--port", type=int, help="External HTTPS port.")
@click.option("--http-port", type=int, help="HTTP port for 'none' strategy.")
@click.option("--ssl-strategy", type=click.Choice(["self-signed", "letsencrypt", "manual", "none"]))
@click.option("--ssl-email", help="Let's Encrypt email.")
@click.option("--ssl-cert", help="Path to SSL certificate (for manual strategy).")
@click.option("--ssl-key", help="Path to SSL private key (for manual strategy).")
@click.option("--opal-version", help="Opal image tag.")
@click.option("--mongo-version", help="MongoDB image tag.")
@click.option("--database", "databases", multiple=True, help="Database spec: type:name:port:user[:version].")
@click.option(
    "--auto-updates",
    "--watchtower",
    "enable_watchtower",
    is_flag=True,
    default=None,
    help="Enable host-side automatic updates (--watchtower is a legacy alias).",
)
@click.option(
    "--no-auto-updates",
    "--no-watchtower",
    "enable_watchtower",
    flag_value=False,
    help="Disable host-side automatic updates (--no-watchtower is a legacy alias).",
)
@click.option(
    "--auto-update-interval",
    "--watchtower-interval",
    "watchtower_interval",
    type=click.IntRange(min=1),
    help="Automatic update interval in hours (legacy: --watchtower-interval).",
)
@click.option("--with-agate", is_flag=True, default=False, help="Enable Agate authentication.")
@click.option("--with-mica", is_flag=True, default=False, help="Enable Mica data portal (implies Agate).")
@click.option("--flavor", type=click.Choice(["opal", "armadillo"]), help="Deployment type.")
@click.option("--preset", help="Apply a preset.")
@click.option("--password", help="Opal admin password (generated if not set).")
@click.option("--yes", is_flag=True, help="Non-interactive mode.")
@click.pass_context
def setup(ctx, name, stack_name, hosts, port, http_port, ssl_strategy, ssl_email,
          ssl_cert, ssl_key, opal_version, mongo_version, databases,
          enable_watchtower, watchtower_interval, with_agate, with_mica, flavor,
          preset, password, yes):
    """Configure a new easy-opal deployment."""
    instance: InstanceContext | None = ctx.obj.get("instance")
    desired_name = name or stack_name or ctx.obj.get("setup_name")
    is_interactive = not yes

    display_header()

    # `setup --name existing` reaches this command without a pre-resolved
    # context. Resolve it before applying runtime-sensitive presets.
    if instance is None and desired_name:
        from src.core.instance_manager import get_instance

        try:
            instance = get_instance(desired_name)
        except ValueError:
            pass

    # A target known up front can be validated before asking configuration
    # questions. An interactively entered name is resolved later, once known.
    runtime = None
    if instance is not None:
        try:
            runtime = _resolve_setup_runtime(
                instance, interactive=is_interactive
            )
        except RuntimeSelectionError as exc:
            raise click.ClickException(str(exc)) from exc

    config = OpalConfig()

    # Apply preset if specified
    if preset:
        from src.presets import apply_preset
        config = apply_preset(config, preset)
        info(f"Preset '{preset}' applied.")

    # Apply flavor from flag early (needed for auto-create)
    if flavor:
        config.flavor = flavor

    if is_interactive:
        info("Welcome to the easy-opal setup wizard!\n")
        # Step 1: flavor and versions (before auto-create so we know the flavor)
        config = _collect_general(config)

    # Resolve the target name before runtime selection, but do not create
    # anything yet. The operator may enter the name of an existing instance,
    # whose binding must win over a new-instance runtime choice.
    chosen = desired_name
    if instance is None:
        from src.core.instance_manager import (
            get_instance,
            next_available_name,
        )

        if not chosen:
            default_name = next_available_name(config.flavor)
            chosen = (
                Prompt.ask(
                    "Instance name (also the Compose stack name)",
                    default=default_name,
                )
                if is_interactive else default_name
            )
        try:
            instance = get_instance(chosen)
        except ValueError:
            pass

    if runtime is None:
        try:
            runtime = _resolve_setup_runtime(
                instance, interactive=is_interactive
            )
        except RuntimeSelectionError as exc:
            raise click.ClickException(str(exc)) from exc

    # A new instance is created only after its runtime has been selected and
    # validated, so cancelling the chooser leaves no partial registry entry.
    if instance is None:
        from src.core.instance_manager import create_instance, set_instance_runtime

        assert chosen is not None
        try:
            instance = create_instance(chosen)
        except ValueError as exc:
            error(str(exc))
            return
        info(f"Created instance '{instance.name}'.")
        set_instance_runtime(instance, runtime.name)

    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    # Runtime binding can be created or changed by another setup between the
    # initial discovery and acquiring the instance lock.  Resolve it again
    # while exclusively owning the instance before generating any artifacts.
    try:
        runtime = get_runtime(instance)
    except RuntimeSelectionError as exc:
        raise click.ClickException(str(exc)) from exc

    was_configured = config_exists(instance)
    previous_config = load_config(instance) if was_configured else None

    # Confirm before overwriting an already-configured instance.
    if was_configured and is_interactive:
        if not Confirm.ask(
            f"Instance '{instance.name}' is already configured. Overwrite its configuration?",
            default=False,
        ):
            info("Setup cancelled.")
            return

    # Preserve an existing instance's stack name (legacy instances may have a
    # stack name that differs from their name); otherwise the name is the stack.
    if was_configured:
        try:
            assert previous_config is not None
            config.stack_name = previous_config.stack_name
        except Exception:
            config.stack_name = instance.name
    else:
        config.stack_name = instance.name

    port_threshold = rootless_port_threshold(runtime)
    if (
        not was_configured
        and port_threshold is not None
        and config.opal_external_port < port_threshold
    ):
        config.opal_external_port = max(8443, port_threshold)
        info(
            "Rootless Podman detected; using HTTPS port "
            f"{config.opal_external_port} by default."
        )

    if is_interactive:
        config = _collect_ssl(config)
        if config.flavor == "opal":
            config = _collect_databases(config)
        config = _collect_watchtower(config, runtime.name)
        config = _collect_backup(config, runtime.name)
        config = _collect_optional_services(config)
    else:
        # Non-interactive: apply CLI flags
        if hosts:
            config.hosts = list(hosts)
        if port:
            config.opal_external_port = port
        if http_port:
            config.opal_http_port = http_port
        if ssl_strategy:
            config.ssl = SSLConfig(strategy=SSLStrategy(ssl_strategy), le_email=ssl_email or "")
            if ssl_strategy == "none":
                config.hosts = []
        if opal_version:
            config.opal_version = opal_version
        if mongo_version:
            config.mongo_version = mongo_version
        if enable_watchtower is not None:
            config.watchtower.enabled = enable_watchtower
        if watchtower_interval is not None:
            config.watchtower.poll_interval_hours = watchtower_interval

        # Optional services
        if with_mica:
            with_agate = True  # Mica requires Agate
        if with_agate:
            config.agate.enabled = True
            config.agate.mail_mode = "mailpit"
        if with_mica:
            config.mica.enabled = True

        # Parse database specs
        for spec in databases:
            parts = spec.split(":")
            if len(parts) < 4:
                error(f"Invalid database spec: {spec}. Expected: type:name:port:user[:version]")
                return
            db_type, name, port_str, user = parts[0], parts[1], parts[2], parts[3]
            version = parts[4] if len(parts) > 4 else "latest"
            config.databases.append(
                DatabaseConfig(type=DatabaseType(db_type), name=name, port=int(port_str), user=user, version=version)
            )

    # Reject runtime-specific features before persisting a configuration that
    # cannot be generated or started on the selected engine.
    from src.services import ServiceRegistry
    try:
        validate_runtime_config(
            runtime, config, port_threshold=port_threshold
        )
        ServiceRegistry(
            config, instance, {}, runtime_name=runtime.name
        ).validate_runtime_support()
    except (RuntimeSelectionError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    manual_certificates: tuple[Path, Path] | None = None
    if config.ssl.strategy == SSLStrategy.MANUAL:
        manual_certificates = _manual_certificate_sources(
            interactive=is_interactive,
            cert_path=ssl_cert,
            key_path=ssl_key,
        )

    try:
        preflight_enabled_schedules(instance, runtime, config)
    except AutoUpdateScheduleError as exc:
        raise click.ClickException(
            f"Scheduled job preflight failed; setup was not saved: {exc}"
        ) from exc

    snapshots = _snapshot_config_artifacts(instance)
    schedule_reconcile_started = False
    registry_update_started = False
    letsencrypt_failed = False
    nginx_was_running = False
    previous_stack_name = (
        previous_config.stack_name
        if previous_config is not None
        else instance.name
    )
    from src.core.instance_manager import update_stack_name

    try:
        instance.ensure_dirs()
        secrets = ensure_secrets(instance, config)

        # Finalize the admin password before rendering artifacts that consume it.
        pw_key = (
            "ARMADILLO_ADMIN_PASSWORD"
            if config.flavor == "armadillo"
            else "OPAL_ADMIN_PASSWORD"
        )
        password_changed = False
        if password:
            secrets[pw_key] = password
            password_changed = True
        elif is_interactive and Confirm.ask(
            "Set your own admin password?", default=False
        ):
            while True:
                custom_pw = Prompt.ask("  Admin password", password=True)
                if custom_pw and custom_pw.strip():
                    secrets[pw_key] = custom_pw
                    password_changed = True
                    break
                error("  Password cannot be empty.")
        if password_changed:
            from src.core.secrets_manager import save_secrets

            save_secrets(secrets, instance)

        if config.ssl.strategy == SSLStrategy.SELF_SIGNED:
            generate_server_cert(instance, config)
        elif manual_certificates is not None:
            cert_file, key_file = manual_certificates
            instance.certs_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cert_file, instance.certs_dir / "opal.crt")
            shutil.copy2(key_file, instance.certs_dir / "opal.key")
            (instance.certs_dir / "opal.crt").chmod(0o644)
            (instance.certs_dir / "opal.key").chmod(0o600)

        generate_nginx_config(config, instance)
        if config.agate.enabled:
            from src.core.agate_config import generate_agate_config

            generate_agate_config(config, instance, secrets)

        # Materialize a complete plan even when the user chooses not to start.
        generate_compose(config, instance)

        # Schedules are installed only after every local artifact is coherent.
        schedule_reconcile_started = True
        reconcile_schedules(instance, runtime, config)

        if config.ssl.strategy == SSLStrategy.LETSENCRYPT:
            info("Requesting Let's Encrypt certificate...")
            try:
                acquisition = obtain_letsencrypt_certificate(config, instance)
            except Exception as exc:
                nginx_was_running = bool(
                    getattr(exc, "nginx_was_running", False)
                )
                raise
            cert_ok = bool(acquisition)
            nginx_was_running = bool(
                getattr(acquisition, "nginx_was_running", False)
            )

            if not cert_ok:
                error("Failed to obtain Let's Encrypt certificate.")
                error("Reverting SSL strategy to 'self-signed'...")
                config.ssl = SSLConfig(strategy=SSLStrategy.SELF_SIGNED)
                generate_server_cert(instance, config)
                generate_nginx_config(config, instance)
                generate_compose(config, instance)
                if nginx_was_running and not restore_running_nginx(
                    config, instance
                ):
                    raise RuntimeError(
                        "The self-signed fallback was generated, but the "
                        "previously running NGINX service could not be restored."
                    )
                letsencrypt_failed = True
            else:
                info("Let's Encrypt challenge completed and NGINX restored.")

        # Commit the source of truth last. Scheduled jobs take the same lock.
        save_config(config, instance)
        registry_update_started = True
        update_stack_name(instance.name, config.stack_name)
    except Exception as exc:
        rollback_errors: list[str] = []
        if schedule_reconcile_started:
            fallback = (
                previous_config
                if previous_config is not None
                else config.model_copy(deep=True)
            )
            if previous_config is None:
                fallback.watchtower.enabled = False
                fallback.backup.enabled = False
                fallback.profile_updater.enabled = False
            try:
                reconcile_schedules(instance, runtime, fallback)
            except Exception as rollback_exc:
                rollback_errors.append(f"schedules: {rollback_exc}")
        files_restored = False
        try:
            _restore_config_artifacts(snapshots)
            files_restored = True
        except Exception as rollback_exc:
            rollback_errors.append(f"files: {rollback_exc}")
        if nginx_was_running and previous_config is not None and files_restored:
            try:
                if not restore_running_nginx(previous_config, instance):
                    rollback_errors.append("NGINX: restart failed")
            except Exception as rollback_exc:
                rollback_errors.append(f"NGINX: {rollback_exc}")
        if registry_update_started:
            try:
                update_stack_name(instance.name, previous_stack_name)
            except Exception as rollback_exc:
                rollback_errors.append(f"registry: {rollback_exc}")

        if rollback_errors:
            raise click.ClickException(
                "Setup failed and rollback was incomplete: "
                f"{exc}; {'; '.join(rollback_errors)}"
            ) from exc
        raise click.ClickException(
            f"Setup failed; previous files and schedules restored: {exc}"
        ) from exc

    admin_pw = secrets[pw_key]
    success(f"Configuration saved to {instance.config_path}")
    console.print(f"\n[bold]Admin password:[/bold] {admin_pw}")
    dim("Run 'easy-opal config show-password' to retrieve it later.")
    if manual_certificates is not None:
        success("Certificates validated and copied.")
    if config.agate.enabled:
        info("Agate email configuration generated.")
    if letsencrypt_failed:
        info(
            "Reverted to self-signed. Fix DNS/firewall and re-run: "
            "easy-opal config change-ssl letsencrypt"
        )
        return
    if config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        success("Let's Encrypt certificate obtained.")

    # Offer to start
    success("\nSetup complete!")
    start = yes or Confirm.ask("Start the stack now?", default=True)
    if start:
        info("Starting...")
        if not compose_up(instance, config):
            raise click.ClickException("Setup was saved, but the stack failed to start.")
        console.print()
        if config.ssl.strategy == SSLStrategy.NONE:
            success(f"Opal is accessible at: http://localhost:{config.opal_http_port}")
        else:
            host = config.hosts[0] if config.hosts else "localhost"
            success(f"Opal is accessible at: https://{host}:{config.opal_external_port}")
        dim(f"Login: administrator / {admin_pw}")
