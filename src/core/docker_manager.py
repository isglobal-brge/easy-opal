import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from ruamel.yaml import YAML
from rich.console import Console

from src.core.config_manager import load_config, BACKUPS_DIR, DATA_DIR, create_snapshot

console = Console()
COMPOSE_TEMPLATE_PATH = Path("src/templates/docker-compose.yml.tpl")
DOCKER_COMPOSE_PATH = Path("docker-compose.yml")


def check_docker_installed():
    """Checks if Docker is installed and running."""
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        # Check for 'docker compose' functionality
        subprocess.run(["docker", "compose", "version"], check=True, capture_output=True)
        # Check if docker daemon is running
        subprocess.run(["docker", "ps"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def generate_compose_file():
    """
    Generates the docker-compose.yml file from the template and config.
    """
    config = load_config()
    if not COMPOSE_TEMPLATE_PATH.exists():
        console.print(f"[bold red]Docker Compose template not found at {COMPOSE_TEMPLATE_PATH}[/bold red]")
        sys.exit(1)

    console.print("[cyan]Generating docker-compose.yml...[/cyan]")

    yaml = YAML()
    with open(COMPOSE_TEMPLATE_PATH, "r") as f:
        compose_data = yaml.load(f)

    # Substitute environment variables in the template
    # ruamel.yaml doesn't support env var substitution out of the box like docker-compose does.
    # We will do it manually for the fields that need it.
    compose_string = Path(COMPOSE_TEMPLATE_PATH).read_text()
    compose_string = compose_string.replace("${PROJECT_NAME}", config["stack_name"])

    # Build the list of rock hosts for discovery
    rock_hosts = []
    for profile in config.get("profiles", []):
        rock_hosts.append(f"http://{profile['name']}:8085")
    
    compose_string = compose_string.replace("${OPAL_ROCK_HOSTS}", ",".join(rock_hosts))

    # Conditionally set ports based on SSL strategy
    strategy = config.get("ssl", {}).get("strategy")
    if strategy == "reverse-proxy":
        # Expose the internal NGINX port 80 to the host
        http_port = config.get("opal_http_port", 8080)
        compose_string = compose_string.replace("#HTTP_PORT_MAPPING", f'- "{http_port}:80"')
        compose_string = compose_string.replace("#HTTPS_PORT_MAPPING", "")
        compose_string = compose_string.replace("${OPAL_HOSTNAME}", "localhost") # Internal default
        compose_string = compose_string.replace("${OPAL_EXTERNAL_PORT}", "80") # Internal default
    else:
        # Standard HTTPS setup
        compose_string = compose_string.replace("#HTTPS_PORT_MAPPING", f'- "{config["opal_external_port"]}:443"')
        compose_string = compose_string.replace("#HTTP_PORT_MAPPING", "")
        # Use the first host as the primary for Opal's proxy settings
        compose_string = compose_string.replace("${OPAL_HOSTNAME}", config["hosts"][0])
        compose_string = compose_string.replace("${OPAL_EXTERNAL_PORT}", str(config["opal_external_port"]))

    # Conditionally add port 80 mapping for letsencrypt (in addition to 443)
    if strategy == "letsencrypt":
        compose_string = compose_string.replace("#LETSENCRYPT_PORT_MAPPING", '- "80:80"')
    else:
        compose_string = compose_string.replace("#LETSENCRYPT_PORT_MAPPING", "")

    compose_data = yaml.load(compose_string)

    # Conditionally remove certbot service if not using letsencrypt
    if config.get("ssl", {}).get("strategy") != "letsencrypt":
        if "certbot" in compose_data.get("services", {}):
            del compose_data["services"]["certbot"]
            console.print("[dim]Certbot service removed (not using Let's Encrypt).[/dim]")

    # Add rock profiles
    if "volumes" not in compose_data:
        compose_data["volumes"] = {}

    for profile in config.get("profiles", []):
        service_name = profile["name"]
        cluster_name = "default" if service_name == "rock" else service_name
        volume_name = f"{config['stack_name']}-{service_name}-data"

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
            "volumes": [f"{volume_name}:/srv"],
            "depends_on": ["opal"],
        }
        compose_data["volumes"][volume_name] = None

    with open(DOCKER_COMPOSE_PATH, "w") as f:
        yaml.dump(compose_data, f)

    console.print(f"[green]docker-compose.yml generated successfully.[/green]")


def run_docker_compose(command: list, project_name: str = None):
    """Helper function to run docker compose commands."""
    if not check_docker_installed():
        console.print("[bold red]Docker is not installed or not running.[/bold red]")
        console.print("Please install Docker Desktop and ensure it's running before using this tool.")
        sys.exit(1)
    
    if project_name is None:
        config = load_config()
        project_name = config["stack_name"]

    base_command = ["docker", "compose", "--project-name", project_name]
    full_command = base_command + command
    
    console.print(f"[bold cyan]Running command: {' '.join(full_command)}[/bold cyan]")
    
    try:
        # By not capturing stdout/stderr, the subprocess will use the parent's terminal,
        # giving the user the full interactive output from docker compose.
        result = subprocess.run(full_command, check=False)

        if result.returncode != 0:
            console.print(f"[bold red]Docker compose command failed with exit code {result.returncode}[/bold red]")
            return False
    except FileNotFoundError:
        console.print("[bold red]docker compose command not found.[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]An error occurred: {e}[/bold red]")
        return False
    return True

def docker_up(remove_orphans=False):
    command = ["up", "-d"]
    if remove_orphans:
        command.append("--remove-orphans")
    return run_docker_compose(command)

def docker_restart(project_name: str = None):
    """Stops and then starts the stack to ensure a clean restart."""
    console.print("[cyan]Stopping the stack...[/cyan]")
    if not run_docker_compose(["down"], project_name=project_name):
        console.print("[bold red]Failed to stop the stack. Aborting restart.[/bold red]")
        return
    
    console.print("\n[cyan]Starting the stack...[/cyan]")
    run_docker_compose(["up", "-d"], project_name=project_name)

def docker_down(project_name: str = None):
    return run_docker_compose(["down"], project_name=project_name)

def docker_reset(project_name: str = None):
    return run_docker_compose(["down", "-v"], project_name=project_name)

def docker_status(project_name: str = None):
    return run_docker_compose(["ps"], project_name=project_name)

def pull_docker_image(image_name: str):
    """Pulls a Docker image and returns True on success, False on failure."""
    console.print(f"[cyan]Attempting to pull image: {image_name}...[/cyan]")
    try:
        # Use a more specific command to just pull an image.
        result = subprocess.run(["docker", "image", "pull", image_name], check=False, capture_output=True, text=True)
        
        if result.returncode != 0:
            console.print(f"[bold red]Failed to pull image '{image_name}'.[/bold red]")
            # Show docker's error message for more context
            console.print(f"[dim]{result.stderr}[/dim]")
            return False
            
    except Exception as e:
        console.print(f"[bold red]An unexpected error occurred while pulling the image: {e}[/bold red]")
        return False
        
    console.print(f"[green]Successfully pulled image: {image_name}[/green]")
    return True 