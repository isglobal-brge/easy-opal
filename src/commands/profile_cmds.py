import click
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from core.config_manager import load_config, save_config
from core.docker_manager import generate_compose_file

console = Console()

@click.group()
def profile():
    """Manage Rock server profiles."""
    pass

@profile.command()
@click.option('--name', prompt='Profile service name (e.g., datashield-rock)', help='The service name for the profile in docker-compose.')
@click.option('--image', prompt='Docker image', default='datashield/rock-base', help='The Docker image for the profile.')
@click.option('--tag', prompt='Image tag', default='latest', help='The tag of the Docker image.')
def add(name, image, tag):
    """Adds a new Rock profile."""
    console.print(f"[cyan]Adding profile '{name}'...[/cyan]")
    config = load_config()

    # Check for duplicate profile names
    if any(p['name'] == name for p in config['profiles']):
        console.print(f"[bold red]A profile with the name '{name}' already exists.[/bold red]")
        return

    new_profile = {"name": name, "image": image, "tag": tag}
    config['profiles'].append(new_profile)
    save_config(config)
    
    console.print(f"[green]Profile '{name}' added to configuration.[/green]")
    generate_compose_file()
    console.print("\nRun 'python3 easy-opal.py up' to apply the changes.")

@profile.command()
def remove():
    """Removes an existing Rock profile."""
    config = load_config()
    profiles = config.get('profiles', [])

    if not profiles:
        console.print("[yellow]No profiles to remove.[/yellow]")
        return

    table = Table(title="Available Profiles")
    table.add_column("Index", style="cyan")
    table.add_column("Name", style="magenta")
    table.add_column("Image", style="green")

    for i, p in enumerate(profiles):
        table.add_row(str(i), p['name'], f"{p['image']}:{p['tag']}")
    
    console.print(table)

    choice = Prompt.ask("Enter the index of the profile to remove", choices=[str(i) for i in range(len(profiles))], show_choices=False)
    profile_to_remove = profiles.pop(int(choice))
    
    config['profiles'] = profiles
    save_config(config)

    console.print(f"[green]Profile '{profile_to_remove['name']}' removed.[/green]")
    generate_compose_file()
    console.print("\nRun 'python3 easy-opal.py up' to apply the changes.")


@profile.command(name="list")
def list_profiles():
    """Lists all configured Rock profiles."""
    config = load_config()
    profiles = config.get('profiles', [])

    if not profiles:
        console.print("[yellow]No profiles configured.[/yellow]")
        return

    table = Table(title="Configured Rock Profiles")
    table.add_column("Name", style="magenta")
    table.add_column("Docker Image", style="green")
    table.add_column("Tag", style="cyan")

    for p in profiles:
        table.add_row(p['name'], p['image'], p['tag'])
    
    console.print(table) 