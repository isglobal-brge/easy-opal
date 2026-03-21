"""Rock server profile management."""

import subprocess
import json

import click
from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.models.config import ProfileConfig
from src.models.instance import InstanceContext
from src.core.config_manager import load_config, save_config, config_exists
from src.core.docker import generate_compose, pull_image
from src.utils.console import console, success, error, info, dim, warning, for_each_instance


def _get_container_status(stack_name: str, profile_name: str) -> str:
    """Check if a profile's container is running."""
    container = f"{stack_name}-{profile_name}"
    try:
        r = subprocess.run(
            ["docker", "inspect", container, "--format", "{{.State.Status}}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else "not created"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


@click.group()
def profile():
    """Manage Rock server profiles."""
    pass


@profile.command()
@click.argument("profiles", nargs=-1)
@click.option("--image", help="Docker image (for single add).")
@click.option("--tag", default="latest", help="Image tag.")
@click.option("--name", help="Service name (for single add).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def add(ctx, profiles, image, tag, name, yes):
    """Add Rock profiles. Pass multiple as image:tag:name or use interactive mode.

    Examples:

      easy-opal profile add

      easy-opal profile add datashield/rock-omics

      easy-opal profile add datashield/rock-omics:latest:rock-omics datashield/rock-dolomite-xenon:latest:rock-xenon
    """
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return

    config = load_config(instance)
    existing = [p.name for p in config.profiles]
    to_add: list[ProfileConfig] = []

    if profiles:
        # Batch mode: parse image:tag:name specs
        for spec in profiles:
            parts = spec.split(":")
            img = parts[0]
            t = parts[1] if len(parts) > 1 else tag
            n = parts[2] if len(parts) > 2 else img.split("/")[-1]
            if n in existing or n in [p.name for p in to_add]:
                warning(f"Skipping '{n}' (already exists).")
                continue
            to_add.append(ProfileConfig(name=n, image=img, tag=t))
    elif image:
        # Single mode via flags
        n = name or image.split("/")[-1]
        if n in existing:
            error(f"Profile '{n}' already exists.")
            return
        to_add.append(ProfileConfig(name=n, image=image, tag=tag))
    else:
        # Interactive mode: collect multiple, pull at the end
        info("Add profiles interactively. Type 'done' when finished.\n")
        while True:
            img = Prompt.ask("  Image (or 'done')", default="done")
            if img == "done":
                break
            t = Prompt.ask("  Tag", default="latest")
            n = Prompt.ask("  Name", default=img.split("/")[-1])
            if n in existing or n in [p.name for p in to_add]:
                warning(f"  '{n}' already exists, skipping.")
                continue
            to_add.append(ProfileConfig(name=n, image=img, tag=t))
            success(f"  Queued: {n} ({img}:{t})")

    if not to_add:
        dim("Nothing to add.")
        return

    # Show summary
    console.print(f"\n[bold]Profiles to add ({len(to_add)}):[/bold]")
    for p in to_add:
        console.print(f"  {p.name} ({p.image}:{p.tag})")

    if not yes and not Confirm.ask("\nProceed?", default=True):
        return

    # Pull all images
    info(f"\nPulling {len(to_add)} image(s)...")
    failed = []
    for p in to_add:
        full = f"{p.image}:{p.tag}"
        if not pull_image(full):
            failed.append(p.name)
            warning(f"  Failed to pull {full}. Skipping '{p.name}'.")

    # Add successful ones to ALL targeted instances
    added = [p for p in to_add if p.name not in failed]
    if not added:
        error("No profiles were added (all pulls failed).")
        return

    def _apply_add(inst):
        cfg = load_config(inst)
        existing_names = [p.name for p in cfg.profiles]
        new = [p for p in added if p.name not in existing_names]
        if not new:
            dim(f"  [{inst.name}] All profiles already exist.")
            return
        cfg.profiles.extend(new)
        save_config(cfg, inst)
        generate_compose(cfg, inst)
        for p in new:
            success(f"  [{inst.name}] Added: {p.name}")

    for_each_instance(ctx, _apply_add)
    info("Run 'easy-opal restart' to apply.")


@profile.command()
@click.argument("names", nargs=-1)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def remove(ctx, names, yes):
    """Remove one or more profiles."""
    instance: InstanceContext = ctx.obj["instance"]
    config = load_config(instance)

    if not config.profiles:
        error("No profiles configured.")
        return

    if not names:
        # Interactive selection
        for i, p in enumerate(config.profiles):
            console.print(f"  {i}. {p.name} ({p.image}:{p.tag})")
        raw = Prompt.ask("Profile index(es) to remove (comma-separated)")
        try:
            indices = [int(x.strip()) for x in raw.split(",")]
            names = tuple(config.profiles[i].name for i in indices if 0 <= i < len(config.profiles))
        except (ValueError, IndexError):
            error("Invalid index.")
            return

    to_remove = [p for p in config.profiles if p.name in names]
    if not to_remove:
        error("No matching profiles found.")
        return

    console.print(f"[bold]Removing {len(to_remove)} profile(s):[/bold]")
    for p in to_remove:
        console.print(f"  {p.name} ({p.image}:{p.tag})")

    if not yes and not Confirm.ask("Confirm?", default=False):
        return

    def _apply_remove(inst):
        cfg = load_config(inst)
        before = len(cfg.profiles)
        cfg.profiles = [p for p in cfg.profiles if p.name not in names]
        removed = before - len(cfg.profiles)
        if removed > 0:
            save_config(cfg, inst)
            generate_compose(cfg, inst)
            success(f"  [{inst.name}] Removed {removed} profile(s)")

    for_each_instance(ctx, _apply_remove)
    info("Run 'easy-opal restart' to apply.")


@profile.command()
@click.argument("old_name")
@click.argument("new_name")
@click.pass_context
def rename(ctx, old_name, new_name):
    """Rename a profile (across all targeted instances)."""
    def _apply_rename(inst):
        cfg = load_config(inst)
        pr = next((p for p in cfg.profiles if p.name == old_name), None)
        if pr:
            pr.name = new_name
            save_config(cfg, inst)
            generate_compose(cfg, inst)
            success(f"  [{inst.name}] Renamed: {old_name} -> {new_name}")

    for_each_instance(ctx, _apply_rename)
    info("Run 'easy-opal restart' to apply.")


@profile.command()
@click.argument("source_name")
@click.argument("new_name")
@click.pass_context
def duplicate(ctx, source_name, new_name):
    """Duplicate a profile with a new name (across all targeted instances)."""
    def _apply_dup(inst):
        cfg = load_config(inst)
        src = next((p for p in cfg.profiles if p.name == source_name), None)
        if src and not any(p.name == new_name for p in cfg.profiles):
            cfg.profiles.append(ProfileConfig(name=new_name, image=src.image, tag=src.tag))
            save_config(cfg, inst)
            generate_compose(cfg, inst)
            success(f"  [{inst.name}] Duplicated: {source_name} -> {new_name}")

    for_each_instance(ctx, _apply_dup)
    info("Run 'easy-opal restart' to apply.")


@profile.command()
def search():
    """Search available DataSHIELD Rock images on Docker Hub."""
    import requests

    info("Searching Docker Hub for DataSHIELD Rock images...\n")
    try:
        resp = requests.get(
            "https://hub.docker.com/v2/repositories/datashield/?page_size=50",
            timeout=10,
        )
        if resp.status_code != 200:
            error("Could not reach Docker Hub.")
            return

        repos = resp.json().get("results", [])
        rock_repos = [r for r in repos if "rock" in r.get("name", "").lower()]

        if not rock_repos:
            dim("No Rock images found.")
            return

        table = Table(title="Available DataSHIELD Rock Images")
        table.add_column("Image", style="cyan bold")
        table.add_column("Description", max_width=50)
        table.add_column("Stars")

        for r in sorted(rock_repos, key=lambda x: x.get("star_count", 0), reverse=True):
            name = f"datashield/{r['name']}"
            desc = (r.get("description") or "")[:50]
            stars = str(r.get("star_count", 0))
            table.add_row(name, desc, stars)

        console.print(table)
        console.print(f"\n[dim]Add with: easy-opal profile add datashield/<image>[/dim]")

    except Exception as e:
        error(f"Search failed: {e}")


@profile.command(name="list")
@click.pass_context
def list_profiles(ctx):
    """List all configured Rock profiles with status."""
    def _list(instance):
        if not config_exists(instance):
            return
        config = load_config(instance)
        if not config.profiles:
            dim("No profiles configured.")
            return

        table = Table(title=f"Rock Profiles ({instance.name})")
        table.add_column("Name", style="cyan bold")
        table.add_column("Image")
        table.add_column("Tag")
        table.add_column("Status")

        for p in config.profiles:
            status = _get_container_status(config.stack_name, p.name)
            if status == "running":
                status_str = "[green]running[/green]"
            elif status == "not created":
                status_str = "[dim]not created[/dim]"
            else:
                status_str = f"[yellow]{status}[/yellow]"
            table.add_row(p.name, p.image, p.tag, status_str)

        console.print(table)

    for_each_instance(ctx, _list)
