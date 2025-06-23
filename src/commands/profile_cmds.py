import click
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.core.config_manager import load_config, save_config
from src.core.docker_manager import generate_compose_file, docker_up, docker_restart, docker_down

console = Console()

@click.group()
def profile():
    """Manage Rock server profiles."""
    pass

@profile.command()
@click.option("--repository", help="The Docker Hub repository (e.g., datashield).")
@click.option("--image", help="The image name (e.g., rock-base).")
@click.option("--tag", help="The image tag (default: latest).")
@click.option("--name", help="The service name for this profile.")
@click.option("--yes", is_flag=True, help="Bypass confirmation and apply changes immediately.")
def add(repository, image, tag, name, yes):
    """Adds a new Rock profile to the configuration."""
    config = load_config()
    is_interactive = not all([repository, image, name])

    if is_interactive:
        console.print("\n[cyan]Adding a new Rock profile (interactive)...[/cyan]")
        repository = Prompt.ask("Enter the Docker Hub repository", default="datashield")
        image = Prompt.ask("Enter the image name (e.g., rock-base)")
        if not image.strip():
            console.print("[bold red]Image name cannot be empty.[/bold red]")
            return
        tag_val = Prompt.ask("Enter the image tag", default="latest")
        default_name = f"rock-{image.replace('rock-', '')}"
        name_val = Prompt.ask("Enter a service name for this profile", default=default_name)
    else:
        console.print("\n[cyan]Adding a new Rock profile (non-interactive)...[/cyan]")
        tag_val = tag or "latest"
        name_val = name

    full_image_name = f"{repository}/{image}"

    # Check for duplicate profile names
    if any(p["name"] == name_val for p in config["profiles"]):
        console.print(f"[bold red]A profile with the service name '{name_val}' already exists.[/bold red]")
        return

    new_profile = {"name": name_val, "image": full_image_name, "tag": tag_val}
    config["profiles"].append(new_profile)
    save_config(config)

    console.print(f"\n[green]Profile '{name_val}' with image '{full_image_name}:{tag_val}' has been added to config.[/green]")

    # Regenerate compose file
    generate_compose_file()

    apply_changes = yes or (is_interactive and Confirm.ask("\n[cyan]Apply changes and restart the stack now?[/cyan]", default=True))
    
    if apply_changes:
        console.print("\n[cyan]Applying changes to the running stack... (This will stop and then restart all services)[/cyan]")
        
        docker_down()
        success = docker_up()

        if not success:
            console.print("[bold red]Failed to start the new profile. The image might not exist.[/bold red]")
            console.print("[yellow]Rolling back configuration...[/yellow]")
            config["profiles"].pop()
            save_config(config)
            generate_compose_file()
            console.print("[cyan]Cleaning up the stack...[/cyan]")
            docker_up(remove_orphans=True)
            console.print("[green]Rollback complete.[/green]")
        else:
            console.print("[green]Stack restarted. The new profile container should be running.[/green]")
            
    else:
        console.print("\n[yellow]Changes have been saved. Run './easy-opal.py up' to apply them later.[/yellow]")

@profile.command()
@click.argument("name", required=False)
@click.option("--yes", is_flag=True, help="Bypass confirmation and apply changes immediately.")
def remove(name, yes):
    """Removes an existing Rock profile, either by name or interactively."""
    config = load_config()
    profiles = config.get("profiles", [])

    if not profiles:
        console.print("[yellow]No profiles to remove.[/yellow]")
        return

    profile_to_remove = None
    if name:
        # Non-interactive mode
        profile_to_remove = next((p for p in profiles if p["name"] == name), None)
        if not profile_to_remove:
            console.print(f"[bold red]Profile '{name}' not found.[/bold red]")
            return
    else:
        # Interactive mode
        table = Table(title="Available Profiles")
        table.add_column("Index", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Image", style="green")

        for i, p in enumerate(profiles):
            table.add_row(str(i), p["name"], f"{p['image']}:{p['tag']}")

        console.print(table)

        choice = Prompt.ask(
            "Enter the index of the profile to remove",
            choices=[str(i) for i in range(len(profiles))],
            show_choices=False,
        )
        profile_to_remove = profiles[int(choice)]

    # Perform the removal
    config["profiles"].remove(profile_to_remove)
    save_config(config)

    console.print(f"[green]Profile '{profile_to_remove['name']}' removed from configuration.[/green]")
    generate_compose_file()

    apply_changes = yes or Confirm.ask("\n[cyan]Apply changes and remove the container now?[/cyan]", default=True)

    if apply_changes:
        console.print("\n[cyan]Applying changes to the running stack...[/cyan]")
        docker_up(remove_orphans=True)
        console.print(
            "[green]Stack updated. The profile's container has been removed.[/green]"
        )
    else:
        console.print("\n[yellow]Changes have been saved. Run './easy-opal.py up' to apply them later.[/yellow]")


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