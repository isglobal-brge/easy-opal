import click
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from core.config_manager import load_config, save_config
from core.docker_manager import generate_compose_file, docker_up, docker_restart, docker_down

console = Console()

@click.group()
def profile():
    """Manage Rock server profiles."""
    pass

@profile.command()
def add():
    """Adds a new Rock profile to the configuration."""
    console.print("\n[cyan]Adding a new Rock profile...[/cyan]")
    
    config = load_config()
    
    # --- Interactive Prompt for Profile ---
    repository = Prompt.ask("Enter the Docker Hub repository", default="datashield")
    image = Prompt.ask("Enter the image name (e.g., rock-base)")
    if not image.strip():
        console.print("[bold red]Image name cannot be empty.[/bold red]")
        return
        
    tag = Prompt.ask("Enter the image tag", default="latest")
    
    default_name = f"rock-{image.replace('rock-', '')}"
    name = Prompt.ask("Enter a service name for this profile", default=default_name)
    
    full_image_name = f"{repository}/{image}"

    # Check for duplicate profile names
    if any(p['name'] == name for p in config['profiles']):
        console.print(f"[bold red]A profile with the service name '{name}' already exists.[/bold red]")
        return

    new_profile = {"name": name, "image": full_image_name, "tag": tag}
    config['profiles'].append(new_profile)
    save_config(config)
    
    console.print(f"\n[green]Profile '{name}' with image '{full_image_name}:{tag}' has been added.[/green]")
    
    # Regenerate compose file
    generate_compose_file()

    if Confirm.ask("\n[cyan]Apply changes and restart the stack now?[/cyan]", default=True):
        console.print("\n[cyan]Applying changes to the running stack... (This will stop and then restart all services)[/cyan]")
        
        # We need to manually do the restart steps to check for failure
        docker_down()
        success = docker_up()

        if not success:
            console.print("[bold red]Failed to start the new profile's container. The image might not exist or another error occurred.[/bold red]")
            console.print("[yellow]Rolling back the configuration...[/yellow]")
            
            config['profiles'].pop()
            save_config(config)
            console.print(f"[yellow]Profile '{name}' has been removed from the configuration.[/yellow]")

            generate_compose_file()
            
            console.print("[cyan]Cleaning up the stack...[/cyan]")
            docker_up(remove_orphans=True)
            console.print("[green]Rollback complete.[/green]")
        else:
            console.print("[green]Stack restarted. The new profile container should be running.[/green]")
            
    else:
        console.print("\n[yellow]Changes have been saved. Run 'python3 easy-opal.py up' to apply them later.[/yellow]")

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
    
    console.print("\n[cyan]Applying changes to the running stack...[/cyan]")
    docker_up(remove_orphans=True)
    console.print("[green]Stack updated. The profile's container has been removed.[/green]")


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