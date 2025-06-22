import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from ruamel.yaml import YAML
from rich.console import Console

from core.config_manager import load_config, BACKUPS_DIR, DATA_DIR

console = Console()
COMPOSE_TEMPLATE_PATH = Path("src/templates/docker-compose.yml.tpl")
DOCKER_COMPOSE_PATH = Path("docker-compose.yml")


def check_docker_installed():
    """Checks if Docker is installed and running."""
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        subprocess.run(["docker-compose", "--version"], check=True, capture_output=True)
        # Check if docker daemon is running
        subprocess.run(["docker", "ps"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def backup_compose_file():
    """Creates a backup of the existing docker-compose.yml file."""
    if DOCKER_COMPOSE_PATH.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = BACKUPS_DIR / f"docker-compose.yml.{timestamp}"
        shutil.copy(DOCKER_COMPOSE_PATH, backup_file)
        console.print(f"[yellow]Backed up existing docker-compose.yml to {backup_file}[/yellow]")


def generate_compose_file():
    """
    Generates the docker-compose.yml file from the template and config.
    """
    config = load_config()
    if not COMPOSE_TEMPLATE_PATH.exists():
        console.print(f"[bold red]Docker Compose template not found at {COMPOSE_TEMPLATE_PATH}[/bold red]")
        sys.exit(1)

    backup_compose_file()

    console.print("[cyan]Generating docker-compose.yml...[/cyan]")

    yaml = YAML()
    with open(COMPOSE_TEMPLATE_PATH, "r") as f:
        compose_data = yaml.load(f)

    # Substitute environment variables in the template
    # ruamel.yaml doesn't support env var substitution out of the box like docker-compose does.
    # We will do it manually for the fields that need it.
    compose_string = Path(COMPOSE_TEMPLATE_PATH).read_text()
    compose_string = compose_string.replace("${PROJECT_NAME}", config["stack_name"])
    # Use the first host as the primary for Opal's proxy settings
    compose_string = compose_string.replace("${OPAL_HOSTNAME}", config["hosts"][0])
    compose_string = compose_string.replace("${OPAL_ADMIN_PASSWORD}", config["opal_admin_password"])
    compose_string = compose_string.replace("${OPAL_EXTERNAL_PORT}", str(config["opal_external_port"]))
    
    # Build the list of rock hosts for discovery
    rock_hosts = []
    for profile in config.get("profiles", []):
        rock_hosts.append(f"http://{profile['name']}:8085")
    
    compose_string = compose_string.replace("${OPAL_ROCK_HOSTS}", ",".join(rock_hosts))

    compose_data = yaml.load(compose_string)

    # Add rock profiles
    for profile in config.get("profiles", []):
        service_name = profile["name"]
        cluster_name = "default" if service_name == "rock" else service_name

        # Create a dedicated volume directory for the profile
        rock_home_path = DATA_DIR / "rock" / service_name
        rock_home_path.mkdir(parents=True, exist_ok=True)

        compose_data["services"][service_name] = {
            "image": f"{profile['image']}:{profile['tag']}",
            "container_name": f"{config['stack_name']}-{service_name}",
            "restart": "always",
            "networks": ["opal-net"],
            "environment": [
                f"ROCK_CLUSTER={cluster_name}",
                f"ROCK_ID={config['stack_name']}-{service_name}",
                "ROCK_ADMINISTRATOR_NAME=administrator",
                "ROCK_ADMINISTRATOR_PASSWORD=password",
                "ROCK_MANAGER_NAME=manager",
                "ROCK_MANAGER_PASSWORD=password",
                "ROCK_USER_NAME=user",
                "ROCK_USER_PASSWORD=password",
            ],
            "volumes": [f"./{rock_home_path.as_posix()}:/srv"],
            "depends_on": ["opal"],
        }

    with open(DOCKER_COMPOSE_PATH, "w") as f:
        yaml.dump(compose_data, f)

    console.print(f"[green]docker-compose.yml generated successfully.[/green]")


def run_docker_compose(command: list):
    """Helper function to run docker-compose commands."""
    if not check_docker_installed():
        console.print("[bold red]Docker or docker-compose is not installed or not running.[/bold red]")
        console.print("Please install Docker Desktop and ensure it's running before using this tool.")
        sys.exit(1)
        
    config = load_config()
    base_command = ["docker-compose", "--project-name", config["stack_name"]]
    full_command = base_command + command
    
    console.print(f"[bold cyan]Running command: {' '.join(full_command)}[/bold cyan]")
    
    try:
        process = subprocess.Popen(full_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in iter(process.stdout.readline, ''):
            print(line, end='')
        process.wait()
        if process.returncode != 0:
            console.print(f"[bold red]Docker-compose command failed with exit code {process.returncode}[/bold red]")
    except FileNotFoundError:
        console.print("[bold red]docker-compose command not found.[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]An error occurred: {e}[/bold red]")
        sys.exit(1)

def docker_up(remove_orphans=False):
    command = ["up", "-d"]
    if remove_orphans:
        command.append("--remove-orphans")
    run_docker_compose(command)

def docker_down():
    run_docker_compose(["down"])

def docker_reset():
    run_docker_compose(["down", "-v"])

def docker_status():
    run_docker_compose(["ps"]) 