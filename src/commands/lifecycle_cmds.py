import click
import shutil
from rich.console import Console
from rich.prompt import Confirm
from src.core.docker_manager import (
    docker_up,
    docker_down,
    docker_restart,
    docker_reset,
    docker_status,
    DOCKER_COMPOSE_PATH,
)
from src.core.config_manager import CONFIG_FILE, CERTS_DIR, DATA_DIR

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
def restart():
    """Restarts the Opal stack."""
    console.print("[bold cyan]Restarting the Opal stack...[/bold cyan]")
    docker_restart()

@click.command()
@click.option("--docker", "delete_docker", is_flag=True, help="Delete Docker containers, networks, and volumes.")
@click.option("--configs", "delete_configs", is_flag=True, help="Delete configuration files.")
@click.option("--certs", "delete_certs", is_flag=True, help="Delete SSL certificates.")
@click.option("--all", is_flag=True, help="Flag to delete everything. Equivalent to using all other flags.")
@click.option("--yes", is_flag=True, help="Bypass the final confirmation prompt.")
def reset(delete_docker, delete_configs, delete_certs, all, yes):
    """Selectively resets parts of the Opal environment."""
    is_interactive = not any([delete_docker, delete_configs, delete_certs, all])

    if all:
        delete_docker = delete_configs = delete_certs = True

    if is_interactive:
        console.print("\n[bold cyan]Interactive Reset Wizard[/bold cyan]")
        console.print("Select which components you want to permanently delete.")
        delete_docker = Confirm.ask(
            "[cyan]Delete all Docker containers, networks, and volumes (includes all Opal/Mongo/Rock data)? This is highly destructive.[/cyan]", default=True
        )
        delete_configs = Confirm.ask(
            "[cyan]Delete configuration files (config.json, docker-compose.yml)?[/cyan]", default=False
        )
        delete_certs = Confirm.ask(
            "[cyan]Delete SSL certificates directory?[/cyan]", default=False
        )

    if not any([delete_docker, delete_configs, delete_certs]):
        console.print("[yellow]Nothing selected. Reset aborted.[/yellow]")
        return

    console.print("\n[bold yellow]Summary of actions to be performed:[/bold yellow]")
    if delete_docker: console.print("- Remove all Docker containers, networks, and named volumes (Opal, Mongo, Rock data).")
    if delete_configs: console.print("- Delete config.json and docker-compose.yml.")
    if delete_certs: console.print("- Delete the SSL certificates directory.")

    proceed = yes or Confirm.ask(
        "\n[bold red]Are you sure you want to proceed with the selected actions?[/bold red]", default=False
    )
    
    if not proceed:
        console.print("[yellow]Reset aborted by user.[/yellow]")
        return

    console.print("\n[bold cyan]Proceeding with reset...[/bold cyan]")

    if delete_docker:
        docker_reset()
        console.print("[green]Docker components and all application data reset.[/green]")

    if delete_configs:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            console.print(f"[yellow]Deleted {CONFIG_FILE}[/yellow]")
        if DOCKER_COMPOSE_PATH.exists():
            DOCKER_COMPOSE_PATH.unlink()
            console.print(f"[yellow]Deleted {DOCKER_COMPOSE_PATH}[/yellow]")

    if delete_certs:
        if CERTS_DIR.exists():
            shutil.rmtree(CERTS_DIR)
            console.print(f"[yellow]Deleted certificates directory: {CERTS_DIR}[/yellow]")

    console.print("\n[green]Reset operation complete.[/green]")

@click.command()
def status():
    """Displays the status of the containers in the stack."""
    console.print("[bold cyan]Opal stack status:[/bold cyan]")
    docker_status() 