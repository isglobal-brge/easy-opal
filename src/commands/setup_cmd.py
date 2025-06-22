import click
import socket
import shutil
from rich.console import Console
from rich.prompt import Prompt, IntPrompt, Confirm
from pathlib import Path

from core.config_manager import (
    save_config,
    get_default_config,
    ensure_directories_exist,
    CONFIG_FILE,
    CERTS_DIR,
    DATA_DIR,
)
from core.ssl_manager import generate_cert_with_mkcert, check_mkcert_installed
from core.nginx_manager import generate_nginx_config
from core.docker_manager import generate_compose_file, check_docker_installed, run_docker_compose

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
            default=False,
        ):
            console.print("[bold yellow]Setup aborted.[/bold yellow]")
            return

    # Dependency checks
    if not check_docker_installed():
        console.print("[bold red]Docker is not installed or not running. Please install and start Docker to continue.[/bold red]")
        return
    
    config = get_default_config()

    if is_interactive:
        # --- Collect Base Config ---
        console.print("[cyan]1. General Configuration[/cyan]")
        config["stack_name"] = Prompt.ask("Enter the stack name", default=config["stack_name"])
        config["opal_external_port"] = IntPrompt.ask("Enter the external HTTPS port for Opal", default=config["opal_external_port"])
        config["opal_admin_password"] = Prompt.ask("Enter the Opal administrator password", default=config["opal_admin_password"], password=True)

        # --- Collect SSL Strategy ---
        console.print("\n[cyan]2. SSL Certificate Configuration[/cyan]")
        strategy = Prompt.ask(
            "Choose an SSL certificate strategy (self-signed, letsencrypt, manual)",
            choices=["self-signed", "letsencrypt", "manual"],
            default="self-signed"
        )
        config["ssl"]["strategy"] = strategy

        # --- Collect Host and Cert-specific Info ---
        if strategy == "self-signed":
            if not check_mkcert_installed():
                console.print("[bold red]mkcert is not installed. Please run './setup.sh' to install it.[/bold red]")
                return

            hosts_list = ["localhost", "127.0.0.1"]
            local_ip = get_local_ip()
            if local_ip not in hosts_list:
                hosts_list.append(local_ip)
            
            console.print(f"Default hosts for self-signed cert are: [green]{', '.join(hosts_list)}[/green]")
            while Confirm.ask("[cyan]Add another hostname or IP?[/cyan]", default=False):
                host = Prompt.ask(f"Enter a hostname or IP address")
                if host not in hosts_list:
                    hosts_list.append(host)
            config["hosts"] = hosts_list

        elif strategy == "manual":
            cert_path = Prompt.ask("Enter the full path to your SSL certificate file (.crt)")
            key_path = Prompt.ask("Enter the full path to your SSL private key file (.key)")
            config["ssl"]["cert_path"] = cert_path
            config["ssl"]["key_path"] = key_path
            host = Prompt.ask("Enter the primary hostname for this certificate (e.g., my-opal.domain.com)")
            config["hosts"] = [host]

        elif strategy == "letsencrypt":
            console.print("[bold yellow]Let's Encrypt requires your server to be publicly accessible on port 80 and 443 with a valid DNS record.[/bold yellow]")
            email = Prompt.ask("Enter your email address for Let's Encrypt renewal notices")
            domain = Prompt.ask("Enter your domain name (e.g., my-opal.domain.com)")
            config["ssl"]["le_email"] = email
            config["hosts"] = [domain]

    else: # Non-interactive mode
        # This part could be expanded to support all strategies via flags.
        # For now, it will implicitly use the "self-signed" defaults for hosts.
        console.print("[cyan]Running non-interactive setup...[/cyan]")
        if stack_name: config["stack_name"] = stack_name
        if hosts: config["hosts"] = list(hosts)
        if port: config["opal_external_port"] = port
        if password: config["opal_admin_password"] = password

    console.print("\n[cyan]Configuration complete. Proceeding with setup...[/cyan]")
    
    # --- Setup Execution ---

    # 1. Create necessary directories (including for letsencrypt)
    ensure_directories_exist()
    le_path = DATA_DIR / "letsencrypt"
    le_path.mkdir(exist_ok=True)
    (le_path / "www").mkdir(exist_ok=True)


    # First, save the configuration so manager functions can read it.
    save_config(config)
    console.print(f"[green]Configuration saved to {CONFIG_FILE}[/green]")

    # 2. Generate initial NGINX config and Docker Compose file
    generate_nginx_config()
    generate_compose_file()
    console.print("[green]Initial docker-compose.yml and nginx.conf generated.[/green]")

    # 3. Generate Certificates based on strategy
    strategy = config.get("ssl", {}).get("strategy")
    
    if strategy == "self-signed":
        cert_path = Path(config["ssl"]["cert_path"])
        key_path = Path(config["ssl"]["key_path"])
        generate_cert_with_mkcert(cert_path, key_path)

    elif strategy == "manual":
        console.print("[cyan]Copying provided certificates...[/cyan]")
        try:
            shutil.copy(config["ssl"]["cert_path"], CERTS_DIR / "opal.crt")
            shutil.copy(config["ssl"]["key_path"], CERTS_DIR / "opal.key")
            # Update config to point to the new location
            config["ssl"]["cert_path"] = str(CERTS_DIR / "opal.crt")
            config["ssl"]["key_path"] = str(CERTS_DIR / "opal.key")
            save_config(config)
            console.print("[green]Certificates copied successfully.[/green]")
        except Exception as e:
            console.print(f"[bold red]Error copying certificates: {e}[/bold red]")
            return

    elif strategy == "letsencrypt":
        domain_args = " ".join([f"-d {d}" for d in config["hosts"]])
        email_arg = f"--email {config['ssl']['le_email']}"
        
        console.print("[cyan]Requesting Let's Encrypt certificate...[/cyan]")
        run_docker_compose(["up", "-d", "nginx"]) # Start nginx to solve challenge
        
        command = f"run --rm certbot certonly --webroot --webroot-path /var/www/certbot {email_arg} {domain_args} --agree-tos --no-eff-email --force-renewal"
        run_docker_compose(command.split())
        
        run_docker_compose(["stop", "nginx"]) # Stop temp nginx
        
        le_cert_path = f"/etc/letsencrypt/live/{config['hosts'][0]}/fullchain.pem"
        le_key_path = f"/etc/letsencrypt/live/{config['hosts'][0]}/privkey.pem"
        
        config["ssl"]["cert_path"] = le_cert_path
        config["ssl"]["key_path"] = le_key_path
        save_config(config)
        console.print("[green]Let's Encrypt certificate obtained successfully.[/green]")


    console.print("\n[bold green]Setup is complete![/bold green]")
    console.print("You can now start the Opal stack by running:")
    console.print("[bold]python3 easy-opal.py up[/bold]")