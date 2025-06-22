import click
import shutil
from pathlib import Path
from rich.console import Console

from core.config_manager import load_config, save_config, CERTS_DIR
from core.ssl_manager import generate_cert_with_mkcert
from core.docker_manager import run_docker_compose

console = Console()


@click.group()
def cert():
    """Manage SSL certificates."""
    pass


@cert.command()
def regenerate():
    """
    Regenerates the SSL certificate based on the strategy in the config file.
    """
    config = load_config()
    strategy = config.get("ssl", {}).get("strategy", "self-signed")

    console.print(f"Regenerating certificate using '{strategy}' strategy...")

    if strategy == "self-signed":
        cert_path = Path(config["ssl"]["cert_path"])
        key_path = Path(config["ssl"]["key_path"])
        generate_cert_with_mkcert(cert_path, key_path)
        console.print(
            "[green]Certificate regenerated. Restart the stack to apply changes ('easy-opal up').[/green]"
        )
    elif strategy == "letsencrypt":
        run_certbot()
        console.print(
            "[green]Let's Encrypt certificate renewed. The stack will automatically pick it up.[/green]"
        )
    elif strategy == "manual":
        console.print(
            "[yellow]Strategy is 'manual'. Please update your certificate files manually at the specified paths.[/yellow]"
        )


def run_certbot():
    """Runs the certbot container to renew certificates."""
    console.print("[cyan]Attempting to renew Let's Encrypt certificate...[/cyan]")
    command = ["run", "--rm", "certbot", "renew"]
    run_docker_compose(command) 