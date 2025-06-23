import click
from pathlib import Path
import shutil
import difflib
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax

from src.core.config_manager import BACKUPS_DIR, CONFIG_FILE, DOCKER_COMPOSE_PATH
from src.core.docker_manager import generate_compose_file

console = Console()

def get_snapshots():
    """Scans the backup directory and returns a sorted list of snapshots."""
    if not BACKUPS_DIR.exists():
        return []
    
    snapshots = []
    for snapshot_dir in BACKUPS_DIR.iterdir():
        if snapshot_dir.is_dir():
            try:
                # Parse timestamp from directory name
                timestamp = datetime.strptime(snapshot_dir.name, "%Y%m%d_%H%M%S")
                snapshots.append({"name": snapshot_dir.name, "path": snapshot_dir, "time": timestamp})
            except ValueError:
                continue # Ignore directories that don't match the timestamp format
    
    return sorted(snapshots, key=lambda x: x["time"], reverse=True)

@click.group(name='backup')
def backup_group():
    """Manage configuration snapshots."""
    pass

@backup_group.command(name="list")
def list_backups():
    """Lists all available configuration snapshots."""
    snapshots = get_snapshots()
    if not snapshots:
        console.print("[yellow]No snapshots found.[/yellow]")
        return

    table = Table(title="Available Snapshots")
    table.add_column("Index", style="cyan")
    table.add_column("Date", style="green")
    table.add_column("Snapshot ID", style="magenta")

    for i, snap in enumerate(snapshots):
        table.add_row(str(i), snap["time"].strftime("%Y-%m-%d %H:%M:%S"), snap["name"])
    
    console.print(table)

@backup_group.command(name="restore")
@click.argument("snapshot_id", required=False)
@click.option("--yes", is_flag=True, help="Bypass confirmation and restore immediately.")
def restore_backup(snapshot_id, yes):
    """Restores the configuration from a snapshot."""
    snapshots = get_snapshots()
    if not snapshots:
        console.print("[yellow]No snapshots to restore.[/yellow]")
        return

    selected_snapshot = None
    if snapshot_id:
        # Non-interactive mode: find the snapshot by ID
        for snap in snapshots:
            if snap["name"] == snapshot_id:
                selected_snapshot = snap
                break
        if not selected_snapshot:
            console.print(f"[bold red]Snapshot ID '{snapshot_id}' not found.[/bold red]")
            return
    else:
        # Interactive mode: let the user choose
        table = Table(title="Available Snapshots")
        table.add_column("Index", style="cyan")
        table.add_column("Date", style="green")
        table.add_column("Snapshot ID", style="magenta")
        for i, snap in enumerate(snapshots):
            table.add_row(str(i), snap["time"].strftime("%Y-%m-%d %H:%M:%S"), snap["name"])
        console.print(table)

        choice = Prompt.ask(
            "\nEnter the index of the snapshot to restore",
            choices=[str(i) for i in range(len(snapshots))],
            show_choices=False,
        )
        selected_snapshot = snapshots[int(choice)]
    
    console.print(f"\n[cyan]Preparing to restore from snapshot:[/cyan] [magenta]{selected_snapshot['name']}[/magenta]")

    # --- Full File Preview ---
    files_to_preview = ["config.json", "docker-compose.yml"]
    for filename in files_to_preview:
        backup_file = selected_snapshot["path"] / filename
        if backup_file.exists():
            console.print(f"\n[bold green]Preview of snapshot's {filename}:[/bold green]")
            content = backup_file.read_text()
            syntax = Syntax(content, "yaml" if ".yml" in filename else "json", theme="monokai", line_numbers=True)
            console.print(syntax)

    # --- Diff Preview ---
    has_changes = False
    for filename in files_to_preview:
        backup_file = selected_snapshot["path"] / filename
        current_file = Path.cwd() / filename
        
        if not backup_file.exists(): continue

        with open(backup_file, 'r') as f_backup: backup_content = f_backup.readlines()
        current_content = []
        if current_file.exists():
            with open(current_file, 'r') as f_current: current_content = f_current.readlines()

        diff = list(difflib.unified_diff(current_content, backup_content, fromfile=f"current/{filename}", tofile=f"backup/{filename}"))
        
        if diff:
            has_changes = True
            console.print(f"\n[bold yellow]Changes for {filename}:[/bold yellow]")
            diff_text = "".join(diff)
            syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=False)
            console.print(syntax)

    if not has_changes:
        console.print("\n[green]No differences found between the current configuration and the snapshot.[/green]")
        if not yes and not Confirm.ask("Do you still want to re-apply this configuration?", default=False):
            console.print("Restore aborted.")
            return

    # --- Confirmation and Restore ---
    proceed = yes or Confirm.ask("\n[bold red]Are you sure you want to overwrite your current configuration with this snapshot?[/bold red]", default=False)
    
    if proceed:
        try:
            for filename in files_to_preview:
                backup_file = selected_snapshot["path"] / filename
                if backup_file.exists():
                    shutil.copy(backup_file, Path.cwd() / filename)
            
            console.print("[green]Configuration successfully restored.[/green]")
            console.print("[yellow]You may need to run './easy-opal restart' for all changes to take effect.[/yellow]")
        except Exception as e:
            console.print(f"[bold red]An error occurred during restore: {e}[/bold red]")
    else:
        console.print("Restore aborted.") 