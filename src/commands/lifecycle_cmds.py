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
from src.core.config_manager import CONFIG_FILE, CERTS_DIR, DATA_DIR, ensure_password_is_set

console = Console()

@click.command()
def up():
    """Ensures the Opal stack is running, restarting it if necessary."""
    if not ensure_password_is_set(): return
    console.print("[bold cyan]Ensuring the Opal stack is up and running...[/bold cyan]")
    # The docker_restart function already handles the down/up sequence.
    docker_restart()

@click.command()
def down():
    """Stops the Opal stack."""
    if not ensure_password_is_set(): return
    console.print("[bold cyan]Stopping the Opal stack...[/bold cyan]")
    docker_down()

@click.command()
@click.option("--containers", "delete_containers", is_flag=True, help="Stop and remove Docker containers and networks.")
@click.option("--volumes", "delete_volumes", is_flag=True, help="Delete Docker volumes (all application data).")
@click.option("--configs", "delete_configs", is_flag=True, help="Delete configuration files.")
@click.option("--certs", "delete_certs", is_flag=True, help="Delete SSL certificates.")
@click.option("--secrets", "delete_secrets", is_flag=True, help="Delete the .env file with the password.")
@click.option("--all", is_flag=True, help="Flag to delete everything. Equivalent to using all other flags.")
@click.option("--yes", is_flag=True, help="Bypass the final confirmation prompt.")
def reset(delete_containers, delete_volumes, delete_configs, delete_certs, delete_secrets, all, yes):
    """Selectively resets parts of the Opal environment."""
    is_interactive = not any([delete_containers, delete_volumes, delete_configs, delete_certs, delete_secrets, all])

    if all:
        delete_containers = delete_volumes = delete_configs = delete_certs = delete_secrets = True

    if is_interactive:
        console.print("\n[bold cyan]Interactive Reset Wizard[/bold cyan]")
        console.print("Select which components you want to permanently delete.")
        delete_containers = Confirm.ask(
            "[cyan]Stop and remove all Docker containers and networks?[/cyan]", default=True
        )
        delete_volumes = Confirm.ask(
            "[bold red]Delete all Docker volumes (includes all Opal/Mongo/Rock data)? This action is highly destructive.[/bold red]", default=False
        )
        delete_configs = Confirm.ask(
            "[cyan]Delete configuration files (config.json, docker-compose.yml)?[/cyan]", default=False
        )
        delete_certs = Confirm.ask(
            "[cyan]Delete SSL certificates directory?[/cyan]", default=False
        )
        delete_secrets = Confirm.ask(
            "[cyan]Delete secrets file (.env)?[/cyan]", default=False
        )

    if not any([delete_containers, delete_volumes, delete_configs, delete_certs, delete_secrets]):
        console.print("[yellow]Nothing selected. Reset aborted.[/yellow]")
        return

    console.print("\n[bold yellow]Summary of actions to be performed:[/bold yellow]")
    if delete_containers: console.print("- Stop and remove all Docker containers and networks.")
    if delete_volumes: console.print("- [bold red]Delete all Docker volumes (Opal, Mongo, Rock data).[/bold red]")
    if delete_configs: console.print("- Delete config.json and docker-compose.yml.")
    if delete_certs: console.print("- Delete the SSL certificates directory.")
    if delete_secrets: console.print("- Delete the secrets file (.env).")

    proceed = yes or Confirm.ask(
        "\n[bold red]Are you sure you want to proceed with the selected actions?[/bold red]", default=False
    )
    
    if not proceed:
        console.print("[yellow]Reset aborted by user.[/yellow]")
        return

    console.print("\n[bold cyan]Proceeding with reset...[/bold cyan]")

    if delete_volumes:
        docker_reset() # This runs 'down -v', removing volumes
        console.print("[green]Docker components and all application data reset.[/green]")
    elif delete_containers:
        docker_down() # This runs 'down', leaving volumes
        console.print("[green]Docker containers and networks removed.[/green]")

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

    if delete_secrets:
        from pathlib import Path
        env_file = Path.cwd() / ".env"
        if env_file.exists():
            env_file.unlink()
            console.print(f"[yellow]Deleted secrets file: {env_file}[/yellow]")

    console.print("\n[green]Reset operation complete.[/green]")

@click.command()
def status():
    """Displays the status of the containers in the stack."""
    if not ensure_password_is_set(): return
    console.print("[bold cyan]Opal stack status:[/bold cyan]")
    docker_status() 