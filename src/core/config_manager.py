import json
import shutil
from pathlib import Path
from typing import Dict, Any, List
from datetime import datetime
from rich.console import Console
from rich.prompt import Prompt

CONFIG_FILE = Path("config.json")
BACKUPS_DIR = Path("backups")
DATA_DIR = Path("data")
MONGO_DATA_DIR = DATA_DIR / "mongo"
NGINX_DIR = DATA_DIR / "nginx"
CERTS_DIR = NGINX_DIR / "certs"
NGINX_CONF_DIR = NGINX_DIR / "conf"
DOCKER_COMPOSE_PATH = Path("docker-compose.yml")

console = Console()
ENV_FILE = Path.cwd() / ".env"

def create_snapshot(reason: str = "Configuration change"):
    """Creates a timestamped snapshot of critical configuration files."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = BACKUPS_DIR / f"{timestamp}"
    
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        
        files_to_back_up = [CONFIG_FILE, DOCKER_COMPOSE_PATH]
        found_any_files = False

        for file_path in files_to_back_up:
            if file_path.exists():
                shutil.copy(file_path, snapshot_dir / file_path.name)
                found_any_files = True
        
        if found_any_files:
            console.print(f"[dim]Created configuration snapshot at [cyan]{snapshot_dir}[/cyan] due to: {reason}[/dim]")
        else:
            console.print("[yellow]No configuration files found to snapshot.[/yellow]")
            snapshot_dir.rmdir()
            
    except Exception as e:
        console.print(f"[bold red]Failed to create configuration snapshot: {e}[/bold red]")

def get_default_config() -> Dict[str, Any]:
    """Returns the default configuration dictionary."""
    return {
        "stack_name": "easy-opal",
        "hosts": ["localhost", "127.0.0.1"],
        "opal_external_port": 443,
        "opal_http_port": 8080,
        "opal_admin_password": "password",
        "profiles": [
            {
                "name": "rock",
                "image": "datashield/rock-base",
                "tag": "latest"
            }
        ],
        "ssl": {
            "strategy": "self-signed",
            "cert_path": str(CERTS_DIR / "opal.crt"),
            "key_path": str(CERTS_DIR / "opal.key"),
            "le_email": ""
        }
    }

def init_config() -> Dict[str, Any]:
    """Initializes and saves the default configuration."""
    config = get_default_config()
    save_config(config)
    return config

def save_config(config: Dict[str, Any]) -> None:
    """Saves the configuration dictionary to the config file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def load_config() -> Dict[str, Any]:
    """Loads the configuration from the config file."""
    if not CONFIG_FILE.exists():
        return init_config()
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def ensure_password_is_set() -> bool:
    """
    Checks if the .env file with the password exists.
    If not, it prompts the user to create it.
    Returns False if the process is aborted, True otherwise.
    """
    if ENV_FILE.exists():
        return True

    console.print("[bold yellow]It looks like the administrator password is not set.[/bold yellow]")
    password = Prompt.ask("Please enter a new Opal administrator password", password=True)

    if not password.strip():
        console.print("[bold red]Password cannot be empty. Aborting.[/bold red]")
        return False

    ENV_FILE.write_text(f"OPAL_ADMIN_PASSWORD={password}")
    console.print(f"[green]Password saved to {ENV_FILE}[/green]")
    return True

def ensure_directories_exist():
    """Ensures that all necessary data and backup directories exist."""
    BACKUPS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    NGINX_DIR.mkdir(exist_ok=True)
    CERTS_DIR.mkdir(exist_ok=True)
    NGINX_CONF_DIR.mkdir(exist_ok=True) 