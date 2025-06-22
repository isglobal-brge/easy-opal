import click
import shutil
from rich.console import Console
from rich.prompt import Confirm
from core.docker_manager import (
    docker_up,
    docker_down,
    docker_reset,
    docker_status,
    DOCKER_COMPOSE_PATH,
)
from core.config_manager import CONFIG_FILE, CERTS_DIR

console = Console()

@click.command()
def up():
    """Starts the Opal stack in detached mode."""
    console.print("[bold cyan]Starting the Opal stack...[/bold cyan]")
    docker_up()

@click.command()
def down():
    """Stops the Opal stack."""
    console.print("[bold cyan]Stopping the Opal stack...[/bold cyan]")
    docker_down()

@click.command()
def reset():
    """Stops the stack, removes all data, and deletes configuration files."""
    if Confirm.ask(
        "[bold red]This will permanently delete all Docker data, certificates, and configuration files. You will have to run setup again. Are you sure?[/bold red]",
        default=False,
    ):
        console.print("[bold cyan]Resetting the Opal stack and configuration...[/bold cyan]")
        docker_reset()

        # Delete the configuration files
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            console.print(f"[yellow]Deleted {CONFIG_FILE}[/yellow]")

        if DOCKER_COMPOSE_PATH.exists():
            DOCKER_COMPOSE_PATH.unlink()
            console.print(f"[yellow]Deleted {DOCKER_COMPOSE_PATH}[/yellow]")

        # Delete certificates directory
        if CERTS_DIR.exists():
            shutil.rmtree(CERTS_DIR)
            console.print(f"[yellow]Deleted certificates directory: {CERTS_DIR}[/yellow]")

        console.print(
            "\n[green]Project has been reset. Run 'python3 easy-opal.py setup' to start over.[/green]"
        )
    else:
        console.print("[yellow]Reset aborted.[/yellow]")

@click.command()
def status():
    """Displays the status of the containers in the stack."""
    console.print("[bold cyan]Opal stack status:[/bold cyan]")
    docker_status() 