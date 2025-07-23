import click
import subprocess
import shutil
from rich.console import Console
from rich.prompt import Confirm

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

def update_dependencies():
    """Check and update Python dependencies if Poetry is available."""
    console.print("[cyan]🔍 Checking for Python dependency updates...[/cyan]")
    
    # Check if Poetry is available
    if shutil.which("poetry"):
        console.print("[blue]📦 Poetry detected, updating dependencies...[/blue]")
        
        # Check if we're in a Poetry project
        try:
            import os
            if os.path.exists("pyproject.toml") and os.path.exists("poetry.lock"):
                console.print("  - Running poetry install to update dependencies")
                
                # Run poetry install --only=main
                try:
                    result = subprocess.run(
                        ["poetry", "install", "--only=main"],
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8"
                    )
                    console.print("[green]✅ Dependencies updated successfully[/green]")
                    return True
                except subprocess.CalledProcessError as e:
                    # Poetry install might return non-zero but still work
                    console.print("[yellow]⚠️  Poetry install completed with warnings (this may be normal)[/yellow]")
                    if e.stdout:
                        console.print(f"[dim]{e.stdout}[/dim]")
                    return True
            else:
                console.print("[yellow]⚠️  Poetry files not found, skipping dependency update[/yellow]")
                return False
        except Exception as e:
            console.print(f"[red]❌ Error updating dependencies: {e}[/red]")
            return False
    else:
        # Check if Python is available and suggest poetry
        if shutil.which("python3") or shutil.which("python"):
            console.print("[yellow]📦 Poetry not found, but Python is available[/yellow]")
            console.print("  - Consider installing Poetry for automatic dependency management")
            console.print("  - You can install it with: curl -sSL https://install.python-poetry.org | python3 -")
            console.print("  - Or run './setup' manually to update dependencies")
        else:
            console.print("[blue]📦 Python/Poetry not detected, skipping dependency update[/blue]")
            console.print("  - If you're using Python, run './setup' manually to update dependencies")
        return False

@click.command()
def update():
    """Checks for and applies updates from the official git repository."""
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