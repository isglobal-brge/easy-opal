import click
import json
from rich.console import Console
from rich.prompt import Prompt, IntPrompt, Confirm
from pathlib import Path
import shutil
import difflib
from datetime import datetime
from rich.table import Table
from rich.syntax import Syntax
import base64
import zlib

from src.core.config_manager import load_config, save_config, BACKUPS_DIR, CONFIG_FILE, ensure_directories_exist, create_snapshot as create_snapshot_from_manager
from src.core.docker_manager import generate_compose_file
from src.core.nginx_manager import generate_nginx_config

console = Console()

@click.group()
def config():
    """Manage easy-opal configuration."""
    pass

@config.command(name="change-password")
@click.argument("password", required=False)
def change_password(password):
    """Changes the Opal administrator password."""
    env_file = Path.cwd() / ".env"

    if not env_file.exists():
        console.print("[yellow].env file not found.[/yellow]")
        if not Confirm.ask("No password is set. Would you like to set one now?", default=True):
            console.print("Aborted.")
            return
            
    new_password = password
    if not new_password:
        new_password = Prompt.ask("Enter the new Opal administrator password", password=True)

    if not new_password or not new_password.strip():
        console.print("[bold red]Password cannot be empty.[/bold red]")
        return
        
    env_file.write_text(f"OPAL_ADMIN_PASSWORD={new_password}")

    console.print("[green]Password updated in .env file.[/green]")
    console.print("\nRun './easy-opal up' to apply the changes.")

@config.command(name="change-port")
@click.argument("port", type=int, required=False)
def change_port(port):
    """Changes the external port for Opal."""
    cfg = load_config()
    
    new_port = port
    if not new_port:
        new_port = IntPrompt.ask("Enter the new external HTTPS port", default=cfg["opal_external_port"])

    # Create a snapshot before making changes
    create_snapshot_from_manager(f"Changed external port to {new_port}")
    cfg["opal_external_port"] = new_port
    save_config(cfg)
    generate_compose_file()
    console.print("[green]Port updated in configuration.[/green]")
    console.print("\nRun './easy-opal up' to apply the changes.")

@config.command(name="show")
def show_config():
    """Displays the current configuration."""
    cfg = load_config()
    # Pretty print the json
    console.print(json.dumps(cfg, indent=4))

@config.command(name="export")
def export_config():
    """Exports the current config.json to a compressed, shareable string."""
    try:
        cfg = load_config()
        # Convert to compact JSON string, then to bytes
        json_bytes = json.dumps(cfg, separators=(',', ':')).encode('utf-8')
        # Compress the bytes
        compressed = zlib.compress(json_bytes, level=9)
        # Encode to a base64 string for easy copying
        encoded_str = base64.b64encode(compressed).decode('utf-8')
        
        console.print("\n[bold green]Configuration Export String:[/bold green]")
        console.print("Copy the string below to import it elsewhere.\n")
        console.print(f"[cyan]{encoded_str}[/cyan]\n")

    except Exception as e:
        console.print(f"[bold red]An error occurred during export: {e}[/bold red]")

@config.command(name="import")
@click.argument("import_string", required=False)
@click.option("--yes", is_flag=True, help="Bypass confirmation prompt.")
def import_config(import_string, yes):
    """Imports a configuration from an export string."""
    if not import_string:
        import_string = Prompt.ask("[cyan]Please paste the configuration export string[/cyan]")
        if not import_string or not import_string.strip():
            console.print("[bold red]Import string cannot be empty.[/bold red]")
            return

    try:
        # Decode from base64
        compressed = base64.b64decode(import_string)
        # Decompress
        json_bytes = zlib.decompress(compressed)
        # Decode bytes to string and parse JSON
        new_cfg = json.loads(json_bytes.decode('utf-8'))

    except (zlib.error, base64.binascii.Error, json.JSONDecodeError) as e:
        console.print(f"[bold red]Invalid import string. Failed to decode configuration: {e}[/bold red]")
        return
    except Exception as e:
        console.print(f"[bold red]An unexpected error occurred: {e}[/bold red]")
        return

    console.print("[bold yellow]New configuration to be imported:[/bold yellow]")
    console.print(json.dumps(new_cfg, indent=2))

    if not yes:
        if not Confirm.ask("\n[bold red]This will overwrite your current config.json. Are you sure you want to proceed?[/bold red]"):
            console.print("Import cancelled.")
            return

    try:
        # Create a snapshot before making changes
        create_snapshot_from_manager("Before configuration import")
        
        # Save the new configuration
        save_config(new_cfg)
        
        # Regenerate dependent files
        generate_nginx_config()
        generate_compose_file()
        
        console.print("\n[green]✅ Configuration successfully imported.[/green]")
        console.print("Run './easy-opal up' to apply the new configuration.")
        
    except Exception as e:
        console.print(f"[bold red]An error occurred while applying the new configuration: {e}[/bold red]")

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

@config.command(name="restore")
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
            syntax = Syntax(content, "yaml" if ".yml" in filename else ("json" if ".json" in filename else "dotenv"), theme="monokai", line_numbers=True)
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
            console.print("[yellow]You may need to run './easy-opal up' for all changes to take effect.[/yellow]")
        except Exception as e:
            console.print(f"[bold red]An error occurred during restore: {e}[/bold red]")
    else:
        console.print("Restore aborted.") 