import click
import socket
from rich.console import Console
from rich.prompt import Prompt, IntPrompt, Confirm
from pathlib import Path

from core.config_manager import (
    save_config,
    get_default_config,
    ensure_directories_exist,
    CONFIG_FILE
)
from core.ssl_manager import generate_cert_with_mkcert, check_mkcert_installed
from core.nginx_manager import generate_nginx_config
from core.docker_manager import generate_compose_file, check_docker_installed

console = Console()

def get_local_ip():
    """Tries to get the local IP address of the machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

@click.command()
@click.option('--stack-name', help='The name of the Docker stack.')
@click.option('--host', 'hosts', multiple=True, help='A hostname or IP for Opal. Can be used multiple times.')
@click.option('--port', help='The external HTTPS port for Opal.', type=int)
@click.option('--password', help='The Opal administrator password.')
def setup(stack_name, hosts, port, password):
    """Guides you through the initial setup or reconfigures the environment."""
    # If no parameters are given, run interactively.
    is_interactive = not stack_name and not hosts and not port and not password

    if is_interactive:
        console.print("[bold cyan]Welcome to the easy-opal setup wizard![/bold cyan]")

    if CONFIG_FILE.exists():
        # In non-interactive mode, we assume overwrite.
        if is_interactive and not Confirm.ask(
            "[yellow]A configuration file already exists. Do you want to overwrite it and start a new setup?[/yellow]",
            default=False
        ):
            console.print("[bold yellow]Setup aborted.[/bold yellow]")
            return

    # Dependency checks
    if not check_docker_installed():
        console.print("[bold red]Docker is not installed or not running. Please install and start Docker to continue.[/bold red]")
        return
    if not check_mkcert_installed():
        console.print("[bold red]mkcert is not installed. This is required for generating trusted local SSL certificates.[/bold red]")
        console.print("Please install it by following the instructions at: https://github.com/FiloSottile/mkcert")
        console.print("On macOS, a simple way is: brew install mkcert")
        return
    
    config = get_default_config()

    if is_interactive:
        console.print("Let's configure your Opal stack.")
        config["stack_name"] = Prompt.ask("Enter the stack name", default=config["stack_name"])
        
        # Interactive hosts
        hosts_list = []
        default_host = "localhost"
        while True:
            host = Prompt.ask(f"Enter a hostname or IP address", default=default_host)
            if host not in hosts_list:
                hosts_list.append(host)
            
            # Suggest local IP
            local_ip = get_local_ip()
            if local_ip not in hosts_list and Confirm.ask(f"[cyan]Also add your local IP '{local_ip}'?[/cyan]", default=True):
                 hosts_list.append(local_ip)

            if not Confirm.ask("[cyan]Add another hostname or IP?[/cyan]", default=False):
                break
            default_host = "" # Clear default for next loop
        config["hosts"] = hosts_list

        config["opal_external_port"] = IntPrompt.ask("Enter the external HTTPS port for Opal", default=config["opal_external_port"])
        config["opal_admin_password"] = Prompt.ask("Enter the Opal administrator password", default=config["opal_admin_password"], password=True)
    else:
        console.print("[cyan]Running non-interactive setup...[/cyan]")
        if stack_name:
            config["stack_name"] = stack_name
        if hosts:
            config["hosts"] = list(hosts)
        if port:
            config["opal_external_port"] = port
        if password:
            config["opal_admin_password"] = password

    console.print("\n[cyan]Configuration complete. Proceeding with setup...[/cyan]")

    # First, save the configuration so manager functions can read it.
    save_config(config)
    console.print(f"[green]Configuration saved to {CONFIG_FILE}[/green]")

    # 1. Create necessary directories
    ensure_directories_exist()

    # 2. Generate SSL certificates
    cert_path = Path(config["ssl"]["cert_path"])
    key_path = Path(config["ssl"]["key_path"])
    generate_cert_with_mkcert(cert_path, key_path)

    # 3. Generate NGINX config
    generate_nginx_config()

    # 4. Generate docker-compose file
    generate_compose_file()

    console.print("\n[bold green]Setup is complete![/bold green]")
    console.print("You can now start the Opal stack by running:")
    console.print("[bold]python3 easy-opal.py up[/bold]")