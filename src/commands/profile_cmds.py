import click
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.core.config_manager import load_config, save_config, ensure_password_is_set, create_snapshot
from src.core.docker_manager import generate_compose_file, docker_up, docker_restart, docker_down, pull_docker_image

console = Console()

@click.group()
def profile():
    """Manage Rock server profiles."""
    pass

@profile.command()
@click.option("--repository", help="The Docker Hub repository (e.g., datashield). Default: datashield.")
@click.option("--image", help="The image name (e.g., rock-base).")
@click.option("--tag", help="The image tag (default: latest).")
@click.option("--name", help="The service name for this profile.")
@click.option("--yes", is_flag=True, help="Bypass confirmation and apply changes immediately.")
def add(repository, image, tag, name, yes):
    """Adds a new Rock profile to the configuration."""
    if not ensure_password_is_set(): return
    config = load_config()
    # Non-interactive mode is active if the essential flags are provided.
    is_interactive = not all([image, name])

    if is_interactive:
        console.print("\n[cyan]Adding a new Rock profile (interactive)...[/cyan]")
        repo_val = Prompt.ask("Enter the Docker Hub repository", default="datashield")
        image_val = Prompt.ask("Enter the image name (e.g., rock-base)")
        if not image_val.strip():
            console.print("[bold red]Image name cannot be empty.[/bold red]")
            return
        tag_val = Prompt.ask("Enter the image tag", default="latest")
        default_name = f"rock-{image_val.replace('rock-', '')}"
        name_val = Prompt.ask("Enter a service name for this profile", default=default_name)
    else:
        console.print("\n[cyan]Adding a new Rock profile (non-interactive)...[/cyan]")
        repo_val = repository or "datashield"
        image_val = image
        tag_val = tag or "latest"
        name_val = name

    # Handle cases where user provides the full repo/image string in the --image flag
    if "/" in image_val and not repo_val:
        full_image_name = image_val
    elif "/" in image_val and repo_val:
        # If both are provided, let's assume the --image flag is the correct full name
        console.print(f"[dim]Warning: Both --repository and a full image path were provided. Using '{image_val}' as the full image name.[/dim]")
        full_image_name = image_val
    else:
        full_image_name = f"{repo_val}/{image_val}"

    # Check for duplicate profile names
    if any(p["name"] == name_val for p in config["profiles"]):
        console.print(f"[bold red]A profile with the service name '{name_val}' already exists.[/bold red]")
        return

    # Before adding to config, try to pull the image to validate it.
    if not pull_docker_image(f"{full_image_name}:{tag_val}"):
        console.print(f"[bold red]Aborting profile add due to invalid image.[/bold red]")
        return

    new_profile = {"name": name_val, "image": full_image_name, "tag": tag_val}
    # Create a snapshot before making changes
    create_snapshot(f"Added profile '{name_val}'")
    config["profiles"].append(new_profile)
    save_config(config)
    generate_compose_file()

    console.print(f"\n[green]Profile '{name_val}' has been added to config.[/green]")

    apply_changes = yes or (is_interactive and Confirm.ask("\n[cyan]Apply changes and restart the stack now by running 'up'?[/cyan]", default=True))
    
    if apply_changes:
        console.print("\n[cyan]Applying changes to the running stack...[/cyan]")
        docker_restart()
        console.print("[green]Stack restarted. The new profile container should be running.[/green]")
    else:
        console.print("\n[yellow]Changes have been saved. Run './easy-opal up' to apply them later.[/yellow]")

@profile.command()
@click.argument("name", required=False)
@click.option("--yes", is_flag=True, help="Bypass confirmation and apply changes immediately.")
def remove(name, yes):
    """Removes an existing Rock profile, either by name or interactively."""
    if not ensure_password_is_set(): return
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

    # Create a snapshot before making changes
    create_snapshot(f"Removed profile '{profile_to_remove['name']}'")
    config["profiles"].remove(profile_to_remove)
    save_config(config)
    generate_compose_file()

    console.print(f"✅ Profile '{profile_to_remove['name']}' removed.")

    apply_changes = yes or Confirm.ask("\n[cyan]Apply changes and restart the stack now by running 'up'?[/cyan]", default=True)

    if apply_changes:
        console.print("\n[cyan]Applying changes to the running stack...[/cyan]")
        docker_up(remove_orphans=True)
        console.print(
            "[green]Stack updated. The profile's container has been removed.[/green]"
        )
    else:
        console.print("\n[yellow]Changes have been saved. Run './easy-opal up' to apply them later.[/yellow]")


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