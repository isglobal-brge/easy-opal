"""Certificate management commands."""

import click

from src.models.instance import InstanceContext
from src.models.enums import SSLStrategy
from src.core.config_manager import load_config, config_exists
from src.core.instance_manager import InstanceLock
from src.core.ssl import generate_server_cert, ensure_ca, get_cert_info
from src.core.docker import run_compose
from src.utils.console import console, success, error, info, warning, require_single_instance


@click.group()
def cert():
    """Manage SSL certificates."""
    pass


@cert.command()
@click.pass_context
def regenerate(ctx):
    """Regenerate the server certificate (preserves the CA)."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return
    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    config = load_config(instance)

    if config.ssl.strategy == SSLStrategy.SELF_SIGNED:
        generate_server_cert(instance, config)
        success("Certificate regenerated. Run 'easy-opal restart' to apply.")

    elif config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        info("Renewing Let's Encrypt certificate...")
        if not run_compose(
            ["--profile", "certbot", "run", "--rm", "certbot", "renew"],
            instance,
            config.stack_name,
        ):
            raise click.ClickException("Certificate renewal failed.")
        if not run_compose(["exec", "nginx", "nginx", "-s", "reload"], instance, config.stack_name):
            raise click.ClickException(
                "Certificate renewed, but NGINX reload failed. Restart the stack manually."
            )
        success("Certificate renewed.")

    elif config.ssl.strategy == SSLStrategy.MANUAL:
        warning("Manual strategy: update your certificate files directly.")

    elif config.ssl.strategy == SSLStrategy.NONE:
        warning("No SSL configured.")


@cert.command(name="info")
@click.pass_context
def cert_info(ctx):
    """Show certificate details."""
    instance: InstanceContext = require_single_instance(ctx)
    ci = get_cert_info(instance)
    if not ci:
        warning("No certificate found.")
        return

    console.print(f"[bold]Subject:[/bold]  {ci['subject']}")
    console.print(f"[bold]Issuer:[/bold]   {ci['issuer']}")
    console.print(f"[bold]Expires:[/bold]  {ci['not_after']}")
    console.print(f"[bold]DNS:[/bold]      {', '.join(ci['dns_names'])}")
    console.print(f"[bold]IPs:[/bold]      {', '.join(ci['ip_addresses'])}")


@cert.command(name="ca-regenerate")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def ca_regenerate(ctx, yes):
    """Force regenerate the local CA (breaks existing trust)."""
    instance: InstanceContext = require_single_instance(ctx)
    if not yes:
        if not click.confirm(
            "This will invalidate any browser trust of the current CA. Continue?"
        ):
            return

    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    # Delete existing CA
    for f in ["ca.crt", "ca.key"]:
        p = instance.certs_dir / f
        if p.exists():
            p.unlink()

    config = load_config(instance)
    ensure_ca(instance)
    generate_server_cert(instance, config)
    success("CA and server certificate regenerated. Run 'easy-opal restart' to apply.")
