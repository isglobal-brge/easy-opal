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
        # Check Docker engine first
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        
        # Check if docker daemon is running
        subprocess.run(["docker", "ps"], check=True, capture_output=True)
        
        # Check Docker version for compatibility warnings
        docker_version = get_docker_version()
        if docker_version:
            # Parse major.minor version for comparison
            try:
                major, minor = map(int, docker_version.split('.')[:2])
                version_num = major * 100 + minor  # e.g., 20.10 -> 2010
                
                if version_num < 113:  # Docker < 1.13
                    console.print(f"[bold red]Warning: Docker version {docker_version} is not supported.[/bold red]")
                    console.print("Please upgrade to Docker 17.06+ for best compatibility.")
                    return False
                elif version_num < 1706:  # Docker 1.13-17.05
                    console.print(f"[bold yellow]Warning: Docker version {docker_version} has limited support.[/bold yellow]")
                    console.print("Some features may not work properly. Consider upgrading to Docker 17.06+.")
                elif version_num < 2010:  # Docker 17.06-20.09
                    console.print(f"[dim]Docker version {docker_version} detected. Some newer features may be limited.[/dim]")
            except (ValueError, AttributeError):
                # If we can't parse the version, continue anyway
                pass
        
        # Check for Docker Compose - try V2 first, then fall back to V1
        compose_available = False
        
        # Try Docker Compose V2 (docker compose)
        try:
            subprocess.run(["docker", "compose", "version"], check=True, capture_output=True)
            compose_available = True
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Try Docker Compose V1 (docker-compose)
            try:
                subprocess.run(["docker-compose", "--version"], check=True, capture_output=True)
                compose_available = True
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
        
        if not compose_available:
            console.print("[bold red]Docker Compose is not available.[/bold red]")
            console.print("Please install Docker Compose (V2 recommended) or docker-compose (V1).")
            return False
            
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

    # --- Set Project-wide values ---
    for service in compose_data["services"].values():
        if service.get("container_name"):
            service["container_name"] = service["container_name"].replace("${PROJECT_NAME}", config["stack_name"])

    # --- Set Opal Version ---
    opal_version = config.get("opal_version", "latest")
    compose_data["services"]["opal"]["image"] = f"obiba/opal:{opal_version}"
    console.print(f"[dim]Using Opal version: {opal_version}[/dim]")

    # --- Configure Opal Service ---
    opal_env = compose_data["services"]["opal"]["environment"]
    rock_hosts = [f"http://{p['name']}:8085" for p in config.get("profiles", [])]
    opal_env["ROCK_HOSTS"] = ",".join(rock_hosts)
    
    # --- Add Additional Database Services ---
    database_instances = config.get("databases", [])
    
    # Initialize volumes if not present
    if "volumes" not in compose_data:
        compose_data["volumes"] = {}
    
    # Process each database instance
    for db in database_instances:
        db_type = db.get("type")
        db_name = db.get("name")
        db_port = db.get("port")
        db_user = db.get("user", "opal")
        db_password = db.get("password")
        db_database = db.get("database", "opaldata")
        
        # Define service name based on instance name
        service_name = db_name
        volume_name = f"{db_name}_data"
        container_name = f"{config['stack_name']}-{db_name}"
        
        # Configure service based on database type
        if db_type == "postgres":
            compose_data["services"][service_name] = {
                "image": "postgres:15",
                "container_name": container_name,
                "restart": "always",
                "environment": {
                    "POSTGRES_USER": db_user,
                    "POSTGRES_PASSWORD": db_password,
                    "POSTGRES_DB": db_database
                },
                "volumes": [f"{volume_name}:/var/lib/postgresql/data"],
                "ports": [f"{db_port}:5432"]
            }
            
            # Configure Opal to connect to this PostgreSQL instance
            env_prefix = db_name.upper().replace("-", "_")
            opal_env[f"{env_prefix}_HOST"] = service_name
            opal_env[f"{env_prefix}_PORT"] = "5432"
            opal_env[f"{env_prefix}_DATABASE"] = db_database
            opal_env[f"{env_prefix}_USER"] = db_user
            opal_env[f"{env_prefix}_PASSWORD"] = db_password
            
            console.print(f"[green]PostgreSQL instance '{db_name}' added on port {db_port}.[/green]")
        
        elif db_type == "mysql":
            compose_data["services"][service_name] = {
                "image": "mysql:8",
                "container_name": container_name,
                "restart": "always",
                "environment": {
                    "MYSQL_ROOT_PASSWORD": db_password,
                    "MYSQL_DATABASE": db_database,
                    "MYSQL_USER": db_user,
                    "MYSQL_PASSWORD": db_password
                },
                "volumes": [f"{volume_name}:/var/lib/mysql"],
                "ports": [f"{db_port}:3306"]
            }
            
            # Configure Opal to connect to this MySQL instance
            env_prefix = db_name.upper().replace("-", "_")
            opal_env[f"{env_prefix}_HOST"] = service_name
            opal_env[f"{env_prefix}_PORT"] = "3306"
            opal_env[f"{env_prefix}_DATABASE"] = db_database
            opal_env[f"{env_prefix}_USER"] = db_user
            opal_env[f"{env_prefix}_PASSWORD"] = db_password
            
            console.print(f"[green]MySQL instance '{db_name}' added on port {db_port}.[/green]")
        
        elif db_type == "mariadb":
            compose_data["services"][service_name] = {
                "image": "mariadb:11",
                "container_name": container_name,
                "restart": "always",
                "environment": {
                    "MARIADB_ROOT_PASSWORD": db_password,
                    "MARIADB_DATABASE": db_database,
                    "MARIADB_USER": db_user,
                    "MARIADB_PASSWORD": db_password
                },
                "volumes": [f"{volume_name}:/var/lib/mysql"],
                "ports": [f"{db_port}:3306"]
            }
            
            # Configure Opal to connect to this MariaDB instance
            env_prefix = db_name.upper().replace("-", "_")
            opal_env[f"{env_prefix}_HOST"] = service_name
            opal_env[f"{env_prefix}_PORT"] = "3306"
            opal_env[f"{env_prefix}_DATABASE"] = db_database
            opal_env[f"{env_prefix}_USER"] = db_user
            opal_env[f"{env_prefix}_PASSWORD"] = db_password
            
            console.print(f"[green]MariaDB instance '{db_name}' added on port {db_port}.[/green]")
        
        # Add volume for this database
        compose_data["volumes"][volume_name] = None

    # --- Configure based on SSL Strategy ---
    strategy = config.get("ssl", {}).get("strategy")
    
    if strategy == "none":
        # Expose the Opal service directly, without our own NGINX.
        http_port = config.get("opal_http_port", 8080)
        compose_data["services"]["opal"]["ports"] = [f"{http_port}:8080"]
        
        # Remove the now-unnecessary NGINX and Certbot services
        if "nginx" in compose_data["services"]: del compose_data["services"]["nginx"]
        if "certbot" in compose_data["services"]: del compose_data["services"]["certbot"]
        
        console.print("[dim]NGINX and Certbot services removed (none/reverse-proxy mode).[/dim]")
        opal_env["OPAL_PROXY_SECURE"] = "false"
        opal_env["OPAL_PROXY_HOST"] = "localhost"
        opal_env["OPAL_PROXY_PORT"] = str(http_port)

    else: # Standard HTTPS strategies
        opal_env["OPAL_PROXY_SECURE"] = "true"
        opal_env["OPAL_PROXY_HOST"] = config["hosts"][0]
        opal_env["OPAL_PROXY_PORT"] = str(config["opal_external_port"])
        
        # Configure NGINX ports
        nginx_ports = [f'{config["opal_external_port"]}:443']
        if strategy == "letsencrypt":
            nginx_ports.append("80:80")
        compose_data["services"]["nginx"]["ports"] = nginx_ports

        # Conditionally remove certbot service if not using letsencrypt
        if strategy != "letsencrypt":
            if "certbot" in compose_data["services"]: del compose_data["services"]["certbot"]

    # Add rock profiles
    if "volumes" not in compose_data: compose_data["volumes"] = {}
    for profile in config.get("profiles", []):
        service_name = profile["name"]
        cluster_name = "default" if service_name == "rock" else service_name
        volume_name = f"{config['stack_name']}-{service_name}-data"

        compose_data["services"][service_name] = {
            "image": f"{profile['image']}:{profile['tag']}",
            "container_name": f"{config['stack_name']}-{service_name}",
            "restart": "always",
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


def get_docker_compose_command():
    """Determines the correct Docker Compose command to use (V2 vs V1)."""
    # Try Docker Compose V2 first (docker compose)
    try:
        subprocess.run(["docker", "compose", "version"], check=True, capture_output=True)
        return ["docker", "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Fall back to Docker Compose V1 (docker-compose)
        try:
            subprocess.run(["docker-compose", "--version"], check=True, capture_output=True)
            return ["docker-compose"]
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None


def run_docker_compose(command: list, project_name: str = None):
    """Helper function to run docker compose commands."""
    if not check_docker_installed():
        console.print("[bold red]Docker is not installed or not running.[/bold red]")
        console.print("Please install Docker Desktop and ensure it's running before using this tool.")
        sys.exit(1)
    
    # Get the appropriate compose command
    compose_cmd = get_docker_compose_command()
    if not compose_cmd:
        console.print("[bold red]Docker Compose is not available.[/bold red]")
        console.print("Please install Docker Compose (V2 recommended) or docker-compose (V1).")
        sys.exit(1)
    
    if project_name is None:
        config = load_config()
        project_name = config["stack_name"]

    # Build the command based on compose version
    if compose_cmd[0] == "docker-compose":
        # V1 syntax: docker-compose --project-name <name> <command>
        base_command = compose_cmd + ["--project-name", project_name]
    else:
        # V2 syntax: docker compose --project-name <name> <command>
        base_command = compose_cmd + ["--project-name", project_name]
    
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

def get_docker_version():
    """Gets the Docker version for compatibility checks."""
    try:
        result = subprocess.run(["docker", "--version"], check=True, capture_output=True, text=True)
        # Parse version from output like "Docker version 20.10.8, build 3967b7d"
        version_str = result.stdout.strip()
        if "version" in version_str:
            # Extract version number (e.g., "20.10.8")
            import re
            match = re.search(r'version\s+(\d+\.\d+\.\d+)', version_str)
            if match:
                return match.group(1)
        return None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def pull_docker_image(image_name: str):
    """Pulls a Docker image with real-time Docker output and returns True on success, False on failure."""
    console.print(f"\n[bold cyan]🐳 Pulling Docker image: {image_name}[/bold cyan]")
    console.print("[dim]" + "="*60 + "[/dim]")
    
    try:
        # Run docker pull with real-time output, no capture
        process = subprocess.Popen(
            ["docker", "pull", image_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        # Stream output in real-time
        for line in iter(process.stdout.readline, ''):
            if line.strip():
                # Print Docker's output directly without Rich formatting to preserve original formatting
                print(line.rstrip())
        
        # Wait for process to complete
        return_code = process.wait()
        
        console.print("[dim]" + "="*60 + "[/dim]")
        
        if return_code != 0:
            console.print(f"[bold red]❌ Failed to pull image '{image_name}' (exit code: {return_code})[/bold red]")
            return False
        else:
            console.print(f"[bold green]✅ Successfully pulled image: {image_name}[/bold green]\n")
            return True
             
    except Exception as e:
        console.print(f"[bold red]❌ An unexpected error occurred while pulling the image: {e}[/bold red]")
        return False