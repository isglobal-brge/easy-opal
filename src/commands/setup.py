"""Interactive and non-interactive setup wizard."""

import click
from rich.prompt import Prompt, IntPrompt, Confirm

from src.models import OpalConfig, SSLConfig, DatabaseConfig, ProfileConfig, WatchtowerConfig, SSLStrategy, DatabaseType
from src.models.instance import InstanceContext
from src.core.config_manager import save_config
from src.core.secrets_manager import ensure_secrets
from src.core.ssl import generate_server_cert
from src.core.nginx import generate_nginx_config
from src.core.docker import check_docker, compose_up, run_compose
from src.utils.console import console, display_header, success, error, info, dim
from src.utils.network import is_port_in_use, find_free_port, get_local_ip, validate_port


def _collect_general(config: OpalConfig) -> OpalConfig:
    """Step 1: Stack name and service versions."""
    info("1. General Configuration")
    config.stack_name = Prompt.ask("Stack name", default=config.stack_name)

    dim("All services default to 'latest'. Press Enter to accept.")
    config.opal_version = Prompt.ask("  Opal version", default=config.opal_version)
    config.mongo_version = Prompt.ask("  MongoDB version", default=config.mongo_version)
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


def _collect_watchtower(config: OpalConfig) -> OpalConfig:
    """Step 4: Watchtower auto-updates."""
    info("\n4. Automatic Updates (Watchtower)")
    dim("Watchtower monitors containers and auto-updates them when new images are available.")

    if Confirm.ask("Enable Watchtower?", default=False):
        config.watchtower.enabled = True
        config.watchtower.poll_interval_hours = IntPrompt.ask(
            "  Check every (hours)", default=config.watchtower.poll_interval_hours
        )
        config.watchtower.cleanup = Confirm.ask("  Remove old images after updates?", default=True)

    return config


@click.command()
@click.option("--stack-name", help="Docker stack name.")
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
@click.option("--watchtower", "enable_watchtower", is_flag=True, default=None)
@click.option("--no-watchtower", "enable_watchtower", flag_value=False)
@click.option("--watchtower-interval", type=int, help="Watchtower interval in hours.")
@click.option("--yes", is_flag=True, help="Non-interactive mode.")
@click.pass_context
def setup(ctx, stack_name, hosts, port, http_port, ssl_strategy, ssl_email,
          ssl_cert, ssl_key, opal_version, mongo_version, databases,
          enable_watchtower, watchtower_interval, yes):
    """Configure a new easy-opal deployment."""
    instance: InstanceContext = ctx.obj["instance"]

    display_header()

    if not check_docker():
        error("Docker is required. Please install Docker and try again.")
        return

    config = OpalConfig()
    is_interactive = not yes

    if is_interactive:
        info("Welcome to the easy-opal setup wizard!\n")
        config = _collect_general(config)
        config = _collect_ssl(config)
        config = _collect_databases(config)
        config = _collect_watchtower(config)
    else:
        # Non-interactive: apply CLI flags
        if stack_name:
            config.stack_name = stack_name
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
        if watchtower_interval:
            config.watchtower.poll_interval_hours = watchtower_interval

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

    # Validate stack name
    from src.core.instance_manager import validate_name, update_stack_name
    err = validate_name(config.stack_name)
    if err:
        error(f"Invalid stack name: {err}")
        return
    update_stack_name(instance.name, config.stack_name)

    # Save config and generate secrets
    instance.ensure_dirs()
    save_config(config, instance)
    secrets = ensure_secrets(instance, config)
    success(f"Configuration saved to {instance.config_path}")

    # Show generated admin password
    admin_pw = secrets.get("OPAL_ADMIN_PASSWORD", "")
    console.print(f"\n[bold]Admin password:[/bold] {admin_pw}")
    dim("Save this password — it won't be shown again.")

    # Generate SSL certs
    if config.ssl.strategy == SSLStrategy.SELF_SIGNED:
        generate_server_cert(instance, config)
    elif config.ssl.strategy == SSLStrategy.MANUAL:
        import shutil
        if is_interactive:
            from rich.prompt import Prompt as P
            cert_src = P.ask("Path to your SSL certificate file (.crt/.pem)")
            key_src = P.ask("Path to your SSL private key file (.key)")
        else:
            cert_src = click.get_current_context().params.get("ssl_cert") or ""
            key_src = click.get_current_context().params.get("ssl_key") or ""

        from pathlib import Path
        cert_file = Path(cert_src)
        key_file = Path(key_src)
        if not cert_file.is_file() or not key_file.is_file():
            error("Certificate or key file not found.")
            return

        # Validate PEM format
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            x509.load_pem_x509_certificate(cert_file.read_bytes())
            load_pem_private_key(key_file.read_bytes(), password=None)
        except Exception as e:
            error(f"Invalid certificate or key: {e}")
            return

        instance.certs_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(cert_src, instance.certs_dir / "opal.crt")
        shutil.copy(key_src, instance.certs_dir / "opal.key")
        success("Certificates validated and copied.")

    # Generate NGINX config
    generate_nginx_config(config, instance)

    # Handle Let's Encrypt
    if config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        info("Requesting Let's Encrypt certificate...")
        info("  Step 1/4: Generating temporary HTTP-only NGINX config...")
        generate_nginx_config(config, instance, acme_only=True)
        from src.core.docker import generate_compose
        generate_compose(config, instance)

        info("  Step 2/4: Starting NGINX for ACME challenge...")
        run_compose(["up", "-d", "nginx"], instance, config.stack_name)

        info("  Step 3/4: Running certbot to obtain certificate...")
        certbot_args = [
            "run", "--rm", "certbot", "certonly", "--webroot",
            "--webroot-path", "/var/www/certbot",
            "--email", config.ssl.le_email,
            "--agree-tos", "--no-eff-email", "--force-renewal",
        ]
        for domain in config.hosts:
            certbot_args.extend(["-d", domain])
        cert_ok = run_compose(certbot_args, instance, config.stack_name)
        run_compose(["stop", "nginx"], instance, config.stack_name)

        if not cert_ok:
            error("Failed to obtain Let's Encrypt certificate.")
            error("Reverting SSL strategy to 'self-signed'...")
            config.ssl = SSLConfig(strategy=SSLStrategy.SELF_SIGNED)
            save_config(config, instance)
            generate_server_cert(instance, config)
            generate_nginx_config(config, instance)
            info("Reverted to self-signed. Fix DNS/firewall and re-run: easy-opal config change-ssl letsencrypt")
            return

        info("  Step 4/4: Generating full HTTPS NGINX config...")
        generate_nginx_config(config, instance, acme_only=False)
        success("Let's Encrypt certificate obtained.")

    # Offer to start
    success("\nSetup complete!")
    start = yes or Confirm.ask("Start the stack now?", default=True)
    if start:
        info("Starting...")
        compose_up(instance, config)
        console.print()
        if config.ssl.strategy == SSLStrategy.NONE:
            success(f"Opal is accessible at: http://localhost:{config.opal_http_port}")
        else:
            host = config.hosts[0] if config.hosts else "localhost"
            success(f"Opal is accessible at: https://{host}:{config.opal_external_port}")
        dim(f"Login: administrator / {admin_pw}")
