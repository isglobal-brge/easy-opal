import click
import json
from rich.console import Console
from rich.prompt import Prompt, IntPrompt

from core.config_manager import load_config, save_config
from core.docker_manager import generate_compose_file

console = Console()

@click.group()
def config():
    """Manage easy-opal configuration."""
    pass

@config.command(name="change-password")
@click.argument("password", required=False)
def change_password(password):
    """Changes the Opal administrator password."""
    cfg = load_config()
    
    new_password = password
    if not new_password:
        new_password = Prompt.ask("Enter the new Opal administrator password", password=True)

    cfg["opal_admin_password"] = new_password
    save_config(cfg)
    console.print("[green]Password updated in configuration.[/green]")
    generate_compose_file()
    console.print("\nRun 'python3 easy-opal.py up' to apply the changes.")

@config.command(name="change-port")
@click.argument("port", type=int, required=False)
def change_port(port):
    """Changes the external port for Opal."""
    cfg = load_config()
    
    new_port = port
    if not new_port:
        new_port = IntPrompt.ask("Enter the new external HTTPS port", default=cfg["opal_external_port"])

    cfg["opal_external_port"] = new_port
    save_config(cfg)
    console.print("[green]Port updated in configuration.[/green]")
    generate_compose_file()
    console.print("\nRun 'python3 easy-opal.py up' to apply the changes.")

@config.command(name="show")
def show_config():
    """Displays the current configuration."""
    cfg = load_config()
    # Pretty print the json
    console.print(json.dumps(cfg, indent=4)) 