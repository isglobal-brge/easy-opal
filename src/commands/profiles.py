"""Rock server profile management."""

import subprocess

import click
from rich.prompt import Prompt, Confirm
from rich.table import Table

from src.models.config import ProfileConfig
from src.models.instance import InstanceContext
from src.core.config_manager import load_config, save_config, config_exists
from src.core.auto_update import COMPOSE_TIMEOUT_SECONDS, PULL_TIMEOUT_SECONDS
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.core.instance_manager import InstanceLock, LOCK_TIMEOUT_SECONDS
from src.core.docker import generate_compose
from src.utils.console import console, success, error, info, dim, warning, for_each_instance, get_instances
from src.utils.images import qualify_image


def _get_container_status(instance: InstanceContext, stack_name: str, profile_name: str) -> str:
    """Check if a profile's container is running."""
    container = f"{stack_name}-{profile_name}"
    try:
        runtime = get_runtime(instance)
        r = runtime.run(
            ["inspect", "--format", "{{.State.Status}}", container],
            capture_output=True, text=True, check=False, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else "not created"
    except RuntimeSelectionError:
        return "runtime unavailable"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"


def _parse_profile_spec(spec: str, default_tag: str) -> ProfileConfig:
    """Parse image[:tag[:name]] without splitting a registry host port."""
    slash = spec.rfind("/")
    prefix = spec[: slash + 1]
    parts = spec[slash + 1 :].split(":")
    if len(parts) > 3 or not parts[0]:
        raise ValueError(f"Invalid profile spec: {spec}")

    image = prefix + parts[0]
    tag = parts[1] if len(parts) >= 2 and parts[1] else default_tag
    name = parts[2] if len(parts) == 3 and parts[2] else parts[0]
    return ProfileConfig(name=name, image=image, tag=tag)


def _run_locked(instance: InstanceContext, operation):
    try:
        with InstanceLock(instance):
            return operation(instance)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _pull_image(runtime, image: str):
    """Pull one image with the same bounded timeout as automatic updates."""
    try:
        return runtime.pull(image, timeout=PULL_TIMEOUT_SECONDS), ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


@click.group()
def profile():
    """Manage Rock server profiles."""
    pass


@profile.command()
@click.argument("profiles", nargs=-1)
@click.option("--image", help="Container image (for single add).")
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
    targets = get_instances(ctx)
    if not targets:
        error("No instance targeted. Use -i <name>.")
        return
    instance: InstanceContext = targets[0]
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return

    config = load_config(instance)
    existing = [p.name for p in config.profiles]
    to_add: list[ProfileConfig] = []

    if profiles:
        # Batch mode: parse image:tag:name specs
        for spec in profiles:
            try:
                parsed = _parse_profile_spec(spec, tag)
            except ValueError as exc:
                error(str(exc))
                return
            if parsed.name in existing or parsed.name in [p.name for p in to_add]:
                warning(f"Skipping '{parsed.name}' (already exists).")
                continue
            to_add.append(parsed)
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
        full = qualify_image(f"{p.image}:{p.tag}")
        pulled = True
        for target in targets:
            result, _pull_error = _pull_image(get_runtime(target), full)
            if result is None or result.returncode != 0:
                pulled = False
        if not pulled:
            failed.append(p.name)
            warning(f"  Failed to pull {full}. Skipping '{p.name}'.")

    # Add successful ones to ALL targeted instances
    added = [p for p in to_add if p.name not in failed]
    if not added:
        raise click.ClickException("No profiles were added (all pulls failed).")

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

    for_each_instance(ctx, lambda inst: _run_locked(inst, _apply_add))
    info("Run 'easy-opal restart' to apply.")


@profile.command()
@click.argument("names", nargs=-1)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def remove(ctx, names, yes):
    """Remove one or more profiles."""
    targets = get_instances(ctx)
    if not targets:
        error("No instance targeted. Use -i <name>.")
        return
    instance: InstanceContext = targets[0]
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

    for_each_instance(ctx, lambda inst: _run_locked(inst, _apply_remove))
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

    for_each_instance(ctx, lambda inst: _run_locked(inst, _apply_rename))
    info("Run 'easy-opal restart' to apply.")


@profile.command()
@click.argument("names", nargs=-1)
@click.option("--no-apply", is_flag=True, help="Pull only, skip container recreation.")
@click.option("--scheduled", is_flag=True, hidden=True)
@click.pass_context
def pull(ctx, names, no_apply, scheduled):
    """Pull profile images and recreate their containers.

    Useful when profiles use mutable tags like ':latest' and the upstream
    image has been updated. Without arguments, pulls all profiles.
    """
    failures: list[str] = []
    if scheduled:
        no_apply = True

    def _pull_and_recreate(inst):
        cfg = load_config(inst)
        if scheduled and not cfg.profile_updater.enabled:
            dim(f"  [{inst.name}] Scheduled profile pulls are disabled; skipping.")
            return
        runtime = get_runtime(inst)
        targets = [p for p in cfg.profiles if not names or p.name in names]

        if names:
            missing = set(names) - {p.name for p in cfg.profiles}
            for m in missing:
                warning(f"  [{inst.name}] Profile '{m}' not found, skipping.")
        if not targets:
            return

        for p in targets:
            full = qualify_image(f"{p.image}:{p.tag}")
            info(f"  [{inst.name}] Pulling {full}...")
            result, pull_error = _pull_image(runtime, full)
            if result is None or result.returncode != 0:
                error(f"  [{inst.name}] Failed: {full}")
                suffix = f" ({pull_error})" if pull_error else ""
                failures.append(f"{inst.name}: pull {full}{suffix}")
                continue

            if no_apply:
                continue

            info(f"  [{inst.name}] Recreating '{p.name}'...")
            result = runtime.compose(
                ["up", "-d", "--force-recreate", "--no-deps", p.name],
                inst, project_name=cfg.stack_name,
                capture_output=True, text=True, check=False,
                timeout=COMPOSE_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                success(f"  [{inst.name}] '{p.name}' refreshed ({p.tag}).")
            else:
                error(f"  [{inst.name}] Recreate failed: {result.stderr.strip()}")
                failures.append(f"{inst.name}: recreate {p.name}")

    def _run(inst):
        try:
            with InstanceLock(
                inst,
                timeout_seconds=LOCK_TIMEOUT_SECONDS if scheduled else 0,
            ):
                return _pull_and_recreate(inst)
        except RuntimeError as exc:
            failures.append(f"{inst.name}: {exc}")

    for_each_instance(ctx, _run)
    if failures:
        raise click.ClickException(
            "Profile refresh failed for " + ", ".join(failures)
        )
    if no_apply:
        info("Run 'easy-opal restart' to apply.")


@profile.command(name="change-version")
@click.argument("name")
@click.argument("tag")
@click.option("--no-apply", is_flag=True, help="Update config and pull but skip container recreation.")
@click.pass_context
def change_version(ctx, name, tag, no_apply):
    """Change a Rock profile's image tag and recreate its container.

    Pulls the new image (or re-pulls if tag is unchanged, useful for :latest)
    and force-recreates only the affected container.
    """
    failures: list[str] = []

    def _apply_change(inst):
        cfg = load_config(inst)
        runtime = get_runtime(inst)
        pr = next((p for p in cfg.profiles if p.name == name), None)
        if not pr:
            warning(f"  [{inst.name}] Profile '{name}' not found, skipping.")
            return

        full_image = qualify_image(f"{pr.image}:{tag}")
        info(f"  [{inst.name}] Pulling {full_image}...")
        result, pull_error = _pull_image(runtime, full_image)
        if result is None or result.returncode != 0:
            error(f"  [{inst.name}] Failed to pull {full_image}.")
            suffix = f" ({pull_error})" if pull_error else ""
            failures.append(f"{inst.name}: pull {full_image}{suffix}")
            return

        old_tag = pr.tag
        pr.tag = tag
        save_config(cfg, inst)
        generate_compose(cfg, inst)
        if old_tag == tag:
            success(f"  [{inst.name}] {name}: re-pulled tag '{tag}'")
        else:
            success(f"  [{inst.name}] {name}: {old_tag} -> {tag}")

        if no_apply:
            return

        info(f"  [{inst.name}] Recreating container '{name}'...")
        result = runtime.compose(
            ["up", "-d", "--force-recreate", "--no-deps", name],
            inst, project_name=cfg.stack_name,
            capture_output=True, text=True, check=False,
            timeout=COMPOSE_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            success(f"  [{inst.name}] '{name}' running on tag '{tag}'.")
        else:
            error(f"  [{inst.name}] Recreate failed: {result.stderr.strip()}")
            failures.append(f"{inst.name}: recreate {name}")

    for_each_instance(ctx, lambda inst: _run_locked(inst, _apply_change))
    if failures:
        raise click.ClickException(
            "Profile version change failed for " + ", ".join(failures)
        )
    if no_apply:
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

    for_each_instance(ctx, lambda inst: _run_locked(inst, _apply_dup))
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
            status = _get_container_status(instance, config.stack_name, p.name)
            if status == "running":
                status_str = "[green]running[/green]"
            elif status == "not created":
                status_str = "[dim]not created[/dim]"
            else:
                status_str = f"[yellow]{status}[/yellow]"
            table.add_row(p.name, p.image, p.tag, status_str)

        console.print(table)

    for_each_instance(ctx, _list)
