"""Rock server profile management."""

import click
from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.models.config import ProfileConfig
from src.models.instance import InstanceContext
from src.core.config_manager import load_config, save_config, config_exists
from src.core.docker import generate_compose, pull_image
from src.utils.console import console, success, error, info


@click.group()
def profile():
    """Manage Rock server profiles."""
    pass


@profile.command()
@click.option("--image", help="Docker image (e.g., datashield/rock-base).")
@click.option("--tag", default="latest", help="Image tag.")
@click.option("--name", help="Service name for this profile.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def add(ctx, image, tag, name, yes):
    """Add a new Rock profile."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return

    config = load_config(instance)
    existing = [p.name for p in config.profiles]

    if not image:
        image = Prompt.ask("Docker image", default="datashield/rock-base")
    if not tag:
        tag = Prompt.ask("Image tag", default="latest")
    if not name:
        name = Prompt.ask("Service name", default=image.split("/")[-1])

    if name in existing:
        error(f"Profile '{name}' already exists.")
        return

    # Validate image exists
    full_image = f"{image}:{tag}"
    info(f"Pulling {full_image} to verify...")
    if not pull_image(full_image):
        error(f"Could not pull {full_image}. Check the image name and tag.")
        return

    config.profiles.append(ProfileConfig(name=name, image=image, tag=tag))
    save_config(config, instance)
    generate_compose(config, instance)
    success(f"Profile '{name}' added ({full_image}).")
    info("Run 'easy-opal restart' to apply.")


@profile.command()
@click.argument("name", required=False)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def remove(ctx, name, yes):
    """Remove a Rock profile."""
    instance: InstanceContext = ctx.obj["instance"]
    config = load_config(instance)

    if not config.profiles:
        error("No profiles configured.")
        return

    if not name:
        for i, p in enumerate(config.profiles):
            console.print(f"  {i}. {p.name} ({p.image}:{p.tag})")
        idx = click.prompt("Profile index to remove", type=int)
        if 0 <= idx < len(config.profiles):
            name = config.profiles[idx].name
        else:
            error("Invalid index.")
            return

    profile = next((p for p in config.profiles if p.name == name), None)
    if not profile:
        error(f"Profile '{name}' not found.")
        return

    if not yes and not Confirm.ask(f"Remove profile '{name}'?", default=False):
        return

    config.profiles.remove(profile)
    save_config(config, instance)
    generate_compose(config, instance)
    success(f"Profile '{name}' removed.")
    info("Run 'easy-opal restart --remove-orphans' to clean up.")


@profile.command(name="list")
@click.pass_context
def list_profiles(ctx):
    """List all configured Rock profiles."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    config = load_config(instance)
    if not config.profiles:
        console.print("[dim]No profiles configured.[/dim]")
        return

    table = Table(title="Rock Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Image", style="bold")
    table.add_column("Tag")

    for p in config.profiles:
        table.add_row(p.name, p.image, p.tag)

    console.print(table)
