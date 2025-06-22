import click
from rich.console import Console
from core.docker_manager import (
    docker_up,
    docker_down,
    docker_restart,
    docker_reset,
    docker_status,
)

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
def reset():
    """Stops the stack and removes all associated data volumes."""
    if click.confirm(
        "[bold red]This will permanently delete all data (mongo database, etc). Are you sure you want to continue?[/bold red]",
        abort=True
    ):
        console.print("[bold cyan]Resetting the Opal stack...[/bold cyan]")
        docker_reset()

@click.command()
def status():
    """Displays the status of the containers in the stack."""
    console.print("[bold cyan]Opal stack status:[/bold cyan]")
    docker_status() 