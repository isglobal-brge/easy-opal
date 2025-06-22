import click
import subprocess
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

@click.command()
def update():
    """Checks for and applies updates from the official git repository."""
    console.print("[cyan]Checking for updates...[/cyan]")

    # 1. Fetch the latest from the remote
    success, output = run_git_command(["fetch"])
    if not success:
        console.print("[bold red]Failed to fetch updates from the remote repository.[/bold red]")
        console.print(f"Error: {output}")
        return

    # 2. Check the status
    success, status_output = run_git_command(["status", "-uno"])
    if not success:
        console.print("[bold red]Failed to get git status.[/bold red]")
        return

    if "Your branch is up to date" in status_output:
        console.print("[green]✅ You are already on the latest version.[/green]")
        return

    if "Your branch is behind" not in status_output:
        console.print("[yellow]Could not determine update status. Your branch may have diverged or has local commits.[/yellow]")
        console.print("Please use 'git status' and 'git pull' manually to update.")
        return
        
    if "Changes not staged for commit" in status_output or "Changes to be committed" in status_output or "Untracked files" in status_output:
        console.print("[bold yellow]⚠️ You have local changes that are not committed.[/bold yellow]")
        console.print("Pulling the latest updates might result in merge conflicts.")
        console.print("It's recommended to commit or stash your changes first.")
        if not Confirm.ask("\n[bold red]Do you want to attempt to pull anyway?[/bold red]", default=False):
            console.print("[yellow]Update aborted.[/yellow]")
            return

    # 3. If behind, prompt to pull
    console.print("[bold yellow]A new version is available![/bold yellow]")
    if Confirm.ask("[cyan]Do you want to download and apply the update now?[/cyan]", default=True):
        console.print("[cyan]Pulling latest changes...[/cyan]")
        success, pull_output = run_git_command(["pull"])
        if success:
            console.print(pull_output)
            console.print("\n[bold green]✅ Update successful![/bold green]")
            console.print("[yellow]If the update included new Python dependencies, please run './setup.sh' again to install them.[/yellow]")
        else:
            console.print("[bold red]Failed to apply updates.[/bold red]")
            console.print(f"Error: {pull_output}") 