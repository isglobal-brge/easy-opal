import subprocess
import sys
from pathlib import Path
from rich.console import Console

from src.core.config_manager import CERTS_DIR, load_config

console = Console()

def check_mkcert_installed():
    """Checks if mkcert is installed on the system."""
    try:
        subprocess.run(["mkcert", "-version"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def generate_cert_with_mkcert(cert_path: Path, key_path: Path):
    """
    Generates a locally-trusted SSL certificate using mkcert.
    Assumes the local CA is already installed (handled by setup.sh).
    """
    if not check_mkcert_installed():
        console.print("[bold red]mkcert is not installed.[/bold red]")
        console.print("Please run the main setup script first to install system dependencies:")
        console.print("[bold]./setup.sh[/bold]")
        sys.exit(1)

    config = load_config()
    hosts = config["hosts"]

    console.print(f"[cyan]Generating certificate for '{' '.join(hosts)}' using mkcert...[/cyan]")

    # mkcert needs to run in a directory where it can write files
    CERTS_DIR.mkdir(parents=True, exist_ok=True)

    command = [
        "mkcert",
        "-cert-file", str(cert_path.name),
        "-key-file", str(key_path.name),
    ] + hosts

    try:
        process = subprocess.run(command, check=True, capture_output=True, text=True, cwd=CERTS_DIR)
        if process.returncode == 0:
            console.print(f"[green]SSL certificate generated successfully in {CERTS_DIR}[/green]")
        else:
            console.print("[bold red]Failed to generate SSL certificate with mkcert.[/bold red]")
            console.print(process.stderr)
            sys.exit(1)
    except subprocess.CalledProcessError as e:
        console.print("[bold red]An error occurred while generating the SSL certificate with mkcert.[/bold red]")
        console.print(e.stderr)
        sys.exit(1) 