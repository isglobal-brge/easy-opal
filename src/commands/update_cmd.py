import click
import subprocess
import shutil
from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()

def run_git_command(command: list) -> (bool, str):
    """Helper to run a git command and return success status and output."""
    try:
        process = subprocess.run(
            ["git"] + command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return True, process.stdout.strip()
    except FileNotFoundError:
        console.print("[bold red]Git is not installed. Please install it to use the update feature.[/bold red]")
        return False, "Git not found"
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()

def find_uv():
    """Find uv executable in common locations."""
    import os
    # Check standard PATH first
    uv_path = shutil.which("uv")
    if uv_path:
        return uv_path

    # Check common installation locations
    common_paths = [
        os.path.expanduser("~/.local/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv"),
        "/usr/local/bin/uv",
        "/opt/homebrew/bin/uv",
    ]
    for path in common_paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None

def pull_opal_image():
    """Pull the Opal Docker image for the currently configured version."""
    from src.core.config_manager import load_config
    from src.core.docker_manager import pull_docker_image

    config = load_config()
    opal_version = config.get("opal_version", "latest")
    image_name = f"obiba/opal:{opal_version}"

    console.print(f"[cyan]Configured Opal version:[/cyan] [bold]{opal_version}[/bold]")
    console.print(f"[cyan]Pulling Docker image:[/cyan] [bold]{image_name}[/bold]\n")

    if pull_docker_image(image_name):
        console.print("\n[green]Opal image updated successfully.[/green]")
        console.print("[yellow]Run './easy-opal restart' to apply the update.[/yellow]")
    else:
        console.print("\n[bold red]Failed to pull the Opal image.[/bold red]")


def update_opal_version(new_version: str):
    """Update the Opal version in config and pull the new image."""
    from src.core.config_manager import load_config, save_config, create_snapshot
    from src.core.docker_manager import generate_compose_file, pull_docker_image

    config = load_config()
    current_version = config.get("opal_version", "latest")

    console.print(f"[cyan]Current Opal version:[/cyan] [bold]{current_version}[/bold]")
    console.print(f"[cyan]New Opal version:[/cyan] [bold]{new_version}[/bold]\n")

    if new_version == current_version:
        console.print("[yellow]Already on this version. Pulling latest image...[/yellow]")
    else:
        # Create snapshot before changing version
        create_snapshot(f"Opal version change: {current_version} -> {new_version}")
        config["opal_version"] = new_version
        save_config(config)
        generate_compose_file()
        console.print("[green]Configuration updated.[/green]")

    # Pull the new image
    image_name = f"obiba/opal:{new_version}"
    if pull_docker_image(image_name):
        console.print("\n[green]Opal updated successfully.[/green]")
        console.print("[yellow]Run './easy-opal restart' to apply the update.[/yellow]")
    else:
        console.print("\n[bold red]Failed to pull the Opal image. The version may not exist.[/bold red]")
        console.print("[yellow]Check available versions at: https://hub.docker.com/r/obiba/opal/tags[/yellow]")


def update_dependencies():
    """Check and update Python dependencies if uv is available."""
    import os
    console.print("[cyan]🔍 Checking for Python dependency updates...[/cyan]")

    # Check if uv is available
    uv_path = find_uv()
    if uv_path:
        console.print("[blue]📦 uv detected, updating dependencies...[/blue]")

        # Check if we're in a uv project
        try:
            if os.path.exists("pyproject.toml"):
                console.print("  - Running uv sync to update dependencies")

                # Run uv sync
                try:
                    result = subprocess.run(
                        [uv_path, "sync"],
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8"
                    )
                    console.print("[green]✅ Dependencies updated successfully[/green]")
                    return True
                except subprocess.CalledProcessError as e:
                    # uv sync might return non-zero but still work
                    console.print("[yellow]⚠️  uv sync completed with warnings (this may be normal)[/yellow]")
                    if e.stdout:
                        console.print(f"[dim]{e.stdout}[/dim]")
                    return True
            else:
                console.print("[yellow]⚠️  pyproject.toml not found, skipping dependency update[/yellow]")
                return False
        except Exception as e:
            console.print(f"[red]❌ Error updating dependencies: {e}[/red]")
            return False
    else:
        # Check if Python is available and suggest uv
        if shutil.which("python3") or shutil.which("python"):
            console.print("[yellow]📦 uv not found, but Python is available[/yellow]")
            console.print("  - Consider installing uv for automatic dependency management")
            console.print("  - You can install it with: curl -LsSf https://astral.sh/uv/install.sh | sh")
            console.print("  - Or run './setup' manually to update dependencies")
        else:
            console.print("[blue]📦 Python/uv not detected, skipping dependency update[/blue]")
            console.print("  - If you're using Python, run './setup' manually to update dependencies")
        return False

@click.command()
@click.option("--opal", is_flag=True, help="Also update/pull the Opal Docker image to the configured version.")
@click.option("--opal-version", help="Update Opal to a specific version (e.g., latest, 5.1, 5.0).")
def update(opal, opal_version):
    """Checks for and applies updates from the official git repository."""

    # Handle Opal version update if requested
    if opal_version:
        update_opal_version(opal_version)
        return

    if opal:
        pull_opal_image()
        return

    console.print("[cyan]Checking for updates...[/cyan]")

    # 1. Safety Check: Ensure we are on the 'main' branch.
    success, current_branch = run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])
    if not success or current_branch != "main":
        console.print("[bold yellow]Update check is only available on the 'main' branch.[/bold yellow]")
        console.print(f"You are currently on branch: [red]{current_branch}[/red]. Please switch to 'main' and try again.")
        return

    # 2. Fetch the latest from the remote repository.
    console.print("[cyan]Fetching latest information from the repository...[/cyan]")
    success, _ = run_git_command(["fetch"])
    if not success:
        console.print("[bold red]Failed to fetch updates from the remote repository.[/bold red]")
        return

    # 3. Reliably check the status against the remote branch.
    success, status_output = run_git_command(["status", "-uno"])
    if not success:
        console.print("[bold red]Failed to get git status.[/bold red]")
        return

    if "Your branch is up to date" in status_output:
        console.print("[green]✅ You are already on the latest version.[/green]")
        return

    if "Your branch is behind" not in status_output:
        console.print("[yellow]Your local 'main' branch has diverged from the remote or has un-pushed commits.[/yellow]")
        console.print("Please resolve the differences manually (e.g., with 'git pull' or 'git reset').")
        return
        
    # 4. An update is available.
    console.print("[bold green]✅ A new version is available![/bold green]")
    console.print("[bold red]The update will override any local changes on the 'main' branch.[/bold red]")
    
    if Confirm.ask("[cyan]It's recommended to keep your tool up-to-date. Update now?[/cyan]", default=True):
        console.print("[cyan]Applying updates forcefully...[/cyan]")
        
        success, reset_output = run_git_command(["reset", "--hard", "origin/main"])
        
        if success:
            console.print(f"[dim]{reset_output}[/dim]")
            console.print("\n[bold green]✅ Update successful![/bold green]")
            
            # Update dependencies automatically
            console.print()
            dependencies_updated = update_dependencies()
            
            if not dependencies_updated:
                console.print("[yellow]If the update included new Python dependencies, please run './setup' again to install them.[/yellow]")
        else:
            console.print("[bold red]Failed to apply updates.[/bold red]")
            console.print(f"Error: {reset_output}") 