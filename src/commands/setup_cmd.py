import click
import socket
import shutil
from rich.console import Console
from rich.prompt import Prompt, IntPrompt, Confirm
from pathlib import Path
import json

from src.core.config_manager import (
    save_config,
    get_default_config,
    ensure_directories_exist,
    CONFIG_FILE,
    CERTS_DIR,
    DATA_DIR,
    ENV_FILE,
    create_snapshot,
)
from src.core.ssl_manager import generate_cert_with_mkcert, check_mkcert_installed
from src.core.nginx_manager import generate_nginx_config
from src.core.docker_manager import generate_compose_file, check_docker_installed, run_docker_compose, docker_reset, docker_down
from src.commands.lifecycle_cmds import reset as interactive_reset

console = Console()

def display_header():
    """Display the colorful easy-opal header with attribution."""
    # ANSI color codes
    RED = '\033[1;31m'
    GREEN = '\033[1;32m'
    LIME_GREEN = '\033[38;5;46m'
    BLUE = '\033[1;34m'
    YELLOW = '\033[1;33m'
    MAGENTA = '\033[1;35m'
    CYAN = '\033[1;36m'
    TURQUOISE = '\033[38;5;73m'
    ORANGE = '\033[38;5;173m'
    BOLD = '\033[1m'
    NC = '\033[0m'  # No Color

    print("")
    print(f"{LIME_GREEN}========================================================={NC}{LIME_GREEN}{BOLD}")
    print("                                                       _ ")
    print("                                                      | |")
    print("  ___   __ _  ___  _   _           ___   _ __    __ _ | |")
    print(" / _ \ / _` |/ __|| | | | ______  / _ \ | '_ \  / _` || |")
    print("|  __/| (_| |\__ \| |_| ||______|| (_) || |_) || (_| || |")
    print(" \___| \__,_||___/ \__, |         \___/ | .__/  \__,_||_|")
    print("                    __/ |               | |              ")
    print("                   |___/                |_|              ")
    print(f"{NC}")
    print(f"{LIME_GREEN}========================================================={NC}")
    print("")
    print(f"Made with ❤️  by {BOLD}\033]8;;https://davidsarratgonzalez.github.io\007David Sarrat González\033]8;;\007{NC}")
    print("")
    print(f"{TURQUOISE}{BOLD}\033]8;;https://brge.isglobal.org\007Bioinformatics Research Group in Epidemiology (BRGE)\033]8;;\007{NC}")
    print(f"{ORANGE}{BOLD}\033]8;;https://www.isglobal.org\007Barcelona Institute for Global Health (ISGlobal)\033]8;;\007{NC}")
    print("")

def is_port_in_use(port: int) -> bool:
    """Checks if a local TCP port is already in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            # We try to bind to all interfaces on that port
            s.bind(("0.0.0.0", port))
            return False
        except OSError:
            # This exception (e.g., EADDRINUSE) means the port is taken
            return True

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
@click.option('--http-port', help="The local HTTP port to expose when using 'none' strategy.", type=int)
@click.option('--password', help='The Opal administrator password.')
@click.option("--ssl-strategy", type=click.Choice(['self-signed', 'letsencrypt', 'manual', 'none']), help="The SSL strategy to use. See [SSL Configuration Guide](./docs/SSL_CONFIGURATION.md) for details.")
@click.option("--ssl-cert-path", help="Path to your certificate file (for 'manual' strategy).")
@click.option("--ssl-key-path", help="Path to your private key file (for 'manual' strategy).")
@click.option("--ssl-email", help="Email for Let's Encrypt renewal notices (for 'letsencrypt' strategy).")
@click.option('--extra-databases', multiple=True, type=click.Choice(['postgres', 'mysql', 'mariadb']), help='Additional database containers to deploy alongside MongoDB. Can be specified multiple times.')
@click.option('--postgres-password', help='Password for PostgreSQL (if postgres is selected).')
@click.option('--mysql-password', help='Password for MySQL/MariaDB (if mysql/mariadb is selected).')
@click.option("--yes", is_flag=True, help="Bypass confirmation prompts for a non-interactive setup.")
@click.option('--reset-containers', is_flag=True, help='[Non-interactive] Stop and remove Docker containers and networks.')
@click.option('--reset-volumes', is_flag=True, help='[Non-interactive] Delete Docker volumes (application data).')
@click.option('--reset-configs', is_flag=True, help='[Non-interactive] Reset config files during setup.')
@click.option('--reset-certs', is_flag=True, help='[Non-interactive] Reset certs during setup.')
@click.option('--reset-secrets', is_flag=True, help='[Non-interactive] Reset secrets file during setup.')
def setup(
    stack_name, hosts, port, http_port, password, ssl_strategy, ssl_cert_path,
    ssl_key_path, ssl_email, extra_databases, postgres_password, mysql_password,
    yes, reset_containers, reset_volumes,
    reset_configs, reset_certs, reset_secrets
):
    """Guides you through the initial setup or reconfigures the environment."""
    
    # Display header
    display_header()
    
    # First, handle the potential teardown of an existing stack.
    if CONFIG_FILE.exists():
        
        if not yes: # Interactive path
            if not Confirm.ask(
                "[yellow]An existing configuration was found. Continuing will overwrite this configuration. Proceed?[/yellow]", 
                default=False
            ):
                console.print("[bold red]Setup aborted by user.[/bold red]")
                return

            # Stop the old stack now that the user has confirmed.
            console.print("\n[cyan]Stopping any running services from the previous setup...[/cyan]")
            try:
                with open(CONFIG_FILE, "r") as f: old_config = json.load(f)
                docker_down(project_name=old_config.get("stack_name", "easy-opal"))
                console.print("[green]Previous services stopped.[/green]")
            except Exception as e:
                console.print(f"[bold red]Could not stop previous services cleanly: {e}[/bold red]")

            console.print("\n[cyan]Running reset wizard to clean up previous installation...[/cyan]")
            try:
                interactive_reset.callback(
                    delete_containers=False, delete_volumes=False, delete_configs=False,
                    delete_certs=False, delete_secrets=False, all=False, yes=False
                )
            except Exception as e:
                console.print(f"[bold red]An error occurred during reset: {e}[/bold red]")
            console.print("[green]Reset complete. Continuing with new setup...[/green]\n")
        
        else: # Non-interactive path with --yes
            # Stop the old stack automatically since --yes implies proceed.
            console.print("\n[cyan]Stopping any running services from the previous setup...[/cyan]")
            try:
                with open(CONFIG_FILE, "r") as f: old_config = json.load(f)
                docker_down(project_name=old_config.get("stack_name", "easy-opal"))
                console.print("[green]Previous services stopped.[/green]")
            except Exception as e:
                console.print(f"[bold red]Could not stop previous services cleanly: {e}[/bold red]")

            if any([reset_containers, reset_volumes, reset_configs, reset_certs, reset_secrets]):
                console.print("[bold yellow]--yes flag provided. Performing specified non-interactive reset...[/bold yellow]")
                try:
                    interactive_reset.callback(
                        delete_containers=reset_containers, delete_volumes=reset_volumes,
                        delete_configs=reset_configs, delete_certs=reset_certs,
                        delete_secrets=reset_secrets, all=False, yes=True
                    )
                except Exception as e:
                    console.print(f"[bold red]An error occurred during non-interactive reset: {e}[/bold red]")
                console.print("[green]Non-interactive reset complete. Continuing with new setup...[/green]\n")

    # Now, proceed with the setup flow.
    # Determine if we can run non-interactively.
    is_interactive = not all([stack_name, hosts, port, password, ssl_strategy]) and not yes
    
    # Check for specific non-interactive requirements
    if not is_interactive and ssl_strategy == 'manual' and not all([ssl_cert_path, ssl_key_path]):
        is_interactive = True # Missing manual cert paths, force interactive
    if not is_interactive and ssl_strategy == 'letsencrypt' and not ssl_email:
        is_interactive = True # Missing letsencrypt email, force interactive

    if is_interactive:
        console.print("[bold cyan]Welcome to the easy-opal setup wizard![/bold cyan]")

    # Dependency checks
    if not check_docker_installed():
        console.print("[bold red]Docker is not installed or not running. Please install and start Docker to continue.[/bold red]")
        return
    
    config = get_default_config()

    if is_interactive:
        # --- Collect Base Config ---
        console.print("[cyan]1. General Configuration[/cyan]")
        config["stack_name"] = Prompt.ask("Enter the stack name", default=config["stack_name"])
        
        # --- Collect SSL Strategy ---
        console.print("\n[cyan]2. SSL Certificate Configuration[/cyan]")
        strategy = Prompt.ask(
            "Choose an SSL certificate strategy",
            choices=["none", "self-signed", "letsencrypt", "manual"],
            default="none"
        )
        config["ssl"]["strategy"] = strategy

        if strategy == "none":
            while True:
                port_val = IntPrompt.ask("Enter the local HTTP port to expose Opal on", default=config["opal_http_port"])
                if not is_port_in_use(port_val):
                    config["opal_http_port"] = port_val
                    break
                else:
                    console.print(f"[bold red]Port {port_val} is already in use. Please choose another one.[/bold red]")
            # No hosts needed for this strategy as it's handled by the external proxy
            config["hosts"] = []
        else:
            # --- Collect Port for HTTPS strategies ---
            while True:
                port_val = IntPrompt.ask("Enter the external HTTPS port for Opal", default=config["opal_external_port"])
                if not is_port_in_use(port_val):
                    config["opal_external_port"] = port_val
                    break
                else:
                    console.print(f"[bold red]Port {port_val} is already in use. Please choose another one.[/bold red]")

            # --- Collect Host and Cert-specific Info ---
            if strategy == "self-signed":
                if not check_mkcert_installed():
                    console.print("[bold red]mkcert is not installed. Please run './setup' to install it.[/bold red]")
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
                cert_path_str = Prompt.ask("Enter the full path to your SSL certificate file (.crt)")
                if not cert_path_str.strip():
                    console.print("[bold red]Certificate path cannot be empty.[/bold red]")
                    return
                if not Path(cert_path_str).is_file():
                    console.print(f"[bold red]File not found at: {cert_path_str}[/bold red]")
                    return

                key_path_str = Prompt.ask("Enter the full path to your SSL private key file (.key)")
                if not key_path_str.strip():
                    console.print("[bold red]Private key path cannot be empty.[/bold red]")
                    return
                if not Path(key_path_str).is_file():
                    console.print(f"[bold red]File not found at: {key_path_str}[/bold red]")
                    return

                host = Prompt.ask("Enter the primary hostname for this certificate (e.g., my-opal.domain.com)")
                if not host.strip():
                    console.print("[bold red]Hostname cannot be empty.[/bold red]")
                    return

                config["ssl"]["cert_path"] = cert_path_str
                config["ssl"]["key_path"] = key_path_str
                config["hosts"] = [host]

            elif strategy == "letsencrypt":
                console.print("[bold yellow]Let's Encrypt requires your server to be publicly accessible on port 80 and 443 with a valid DNS record.[/bold yellow]")
                email = Prompt.ask("Enter your email address for Let's Encrypt renewal notices")
                if not email.strip():
                    console.print("[bold red]Email address cannot be empty.[/bold red]")
                    return
                domain = Prompt.ask("Enter your domain name (e.g., my-opal.domain.com)")
                if not domain.strip():
                    console.print("[bold red]Domain name cannot be empty.[/bold red]")
                    return
                config["ssl"]["le_email"] = email
                config["hosts"] = [domain]
        
        # --- Database Configuration ---
        config["databases"] = {
            "mongodb": {"enabled": True}  # MongoDB is always enabled
        }
        
        if is_interactive:
            console.print("\n[cyan]Database Configuration[/cyan]")
            console.print("MongoDB is the primary database for Opal metadata (always enabled).")
            
            if Confirm.ask("[cyan]Would you like to deploy additional database containers for data sources?[/cyan]", default=False):
                console.print("\nSelect additional databases to deploy as data sources:")
                console.print("These databases can be connected to Opal for data storage and analysis.")
                
                if Confirm.ask("  • Deploy PostgreSQL container?", default=False):
                    config["databases"]["postgres"] = {"enabled": True}
                    pg_pass = Prompt.ask("    PostgreSQL password", default="postgres_password", password=True)
                    config["databases"]["postgres"]["password"] = pg_pass
                
                if Confirm.ask("  • Deploy MySQL container?", default=False):
                    config["databases"]["mysql"] = {"enabled": True}
                    mysql_pass = Prompt.ask("    MySQL root password", default="mysql_password", password=True)
                    config["databases"]["mysql"]["password"] = mysql_pass
                
                if Confirm.ask("  • Deploy MariaDB container?", default=False):
                    config["databases"]["mariadb"] = {"enabled": True}
                    maria_pass = Prompt.ask("    MariaDB root password", default="mariadb_password", password=True)
                    config["databases"]["mariadb"]["password"] = maria_pass

    else: # Non-interactive mode
        console.print("[cyan]Running non-interactive setup...[/cyan]")
        if stack_name: config["stack_name"] = stack_name
        if hosts: config["hosts"] = list(hosts)
        if port: config["opal_external_port"] = port
        if http_port: config["opal_http_port"] = http_port
        if password:
            # Save password to .env file for non-interactive setup
            (Path.cwd() / ".env").write_text(f"OPAL_ADMIN_PASSWORD={password}")
        elif is_interactive:
            # Prompt for password and save to .env
            password = Prompt.ask("Enter the Opal administrator password", default=config["opal_admin_password"], password=True)
            (Path.cwd() / ".env").write_text(f"OPAL_ADMIN_PASSWORD={password}")
        else:
            # Handle case where password is not provided in non-interactive mode
            console.print("[bold red]Opal administrator password must be provided in non-interactive mode using the --password flag.[/bold red]")
            return
        
        # SSL non-interactive config
        config["ssl"]["strategy"] = ssl_strategy
        if ssl_strategy == "manual":
            config["ssl"]["cert_path"] = ssl_cert_path
            config["ssl"]["key_path"] = ssl_key_path
        elif ssl_strategy == "letsencrypt":
            config["ssl"]["le_email"] = ssl_email
        elif ssl_strategy == "none":
            if http_port: config["opal_http_port"] = http_port
            # No hosts needed for this strategy
            config["hosts"] = []
        
        # Database configuration for non-interactive mode
        config["databases"] = {
            "mongodb": {"enabled": True}  # MongoDB is always enabled
        }
        
        if extra_databases:
            for db in extra_databases:
                config["databases"][db] = {"enabled": True}
                if db == "postgres" and postgres_password:
                    config["databases"]["postgres"]["password"] = postgres_password
                elif db == "mysql" and mysql_password:
                    config["databases"]["mysql"]["password"] = mysql_password
                elif db == "mariadb" and mysql_password:
                    config["databases"]["mariadb"]["password"] = mysql_password
                else:
                    # Set default passwords if not provided
                    if db == "postgres":
                        config["databases"]["postgres"]["password"] = "postgres_password"
                    elif db == "mysql":
                        config["databases"]["mysql"]["password"] = "mysql_password"
                    elif db == "mariadb":
                        config["databases"]["mariadb"]["password"] = "mariadb_password"

    # --- Password Handling ---
    if is_interactive:
        if ENV_FILE.exists():
            if Confirm.ask("\n[yellow]An administrator password is already set. Do you want to change it?[/yellow]", default=False):
                while True:
                    new_password = Prompt.ask("Enter the new Opal administrator password", password=True)
                    if new_password.strip():
                        (ENV_FILE).write_text(f"OPAL_ADMIN_PASSWORD={new_password}")
                        console.print("[green]Password updated.[/green]")
                        break
                    else:
                        console.print("[bold red]Password cannot be empty. Please try again.[/bold red]")
            else:
                console.print("[green]Keeping existing password.[/green]")
        else:
            while True:
                new_password = Prompt.ask("\nEnter the Opal administrator password", password=True)
                if new_password.strip():
                    (ENV_FILE).write_text(f"OPAL_ADMIN_PASSWORD={new_password}")
                    console.print("[green]Password saved.[/green]")
                    break
                else:
                    console.print("[bold red]Password cannot be empty. Please try again.[/bold red]")
    else: # Non-interactive
        if not password:
            console.print("[bold red]--password flag is required for non-interactive setup.[/bold red]")
            return
        (ENV_FILE).write_text(f"OPAL_ADMIN_PASSWORD={password}")
        console.print("[green]Administrator password saved to .env file.[/green]")

    # --- Final Steps ---
    
    # Remove password from config object just in case it's there from defaults
    if "opal_admin_password" in config:
        del config["opal_admin_password"]

    console.print("\n[cyan]Configuration complete. Proceeding with setup...[/cyan]")
    
    # 1. Create necessary directories
    ensure_directories_exist()

    # First, save the configuration so manager functions can read it.
    save_config(config)
    console.print(f"[green]Configuration saved to {CONFIG_FILE}[/green]")
    
    # 2. Generate initial NGINX config and Docker Compose file
    create_snapshot("Initial setup configuration")
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
        
        # We need to start nginx temporarily to solve the HTTP-01 challenge
        run_docker_compose(["up", "-d", "nginx"])
        
        command = f"run --rm certbot certonly --webroot --webroot-path /var/www/certbot {email_arg} {domain_args} --agree-tos --no-eff-email --force-renewal"
        cert_success = run_docker_compose(command.split())
        
        # Always stop the temporary nginx container
        run_docker_compose(["stop", "nginx"])
        
        if not cert_success:
            console.print("[bold red]Failed to obtain Let's Encrypt certificate. Please check the logs above.[/bold red]")
            console.print("[yellow]Your configuration has been saved, but you will need to run the setup again to retry certificate generation.[/yellow]")
            return

        le_cert_path = f"/etc/letsencrypt/live/{config['hosts'][0]}/fullchain.pem"
        le_key_path = f"/etc/letsencrypt/live/{config['hosts'][0]}/privkey.pem"
        
        config["ssl"]["cert_path"] = le_cert_path
        config["ssl"]["key_path"] = le_key_path
        save_config(config)
        console.print("[green]Let's Encrypt certificate obtained successfully.[/green]")

    console.print("\n[bold green]Setup is complete![/bold green]")
    console.print("You can now start the Opal stack by running:")
    console.print("[bold yellow]./easy-opal up[/bold yellow]")

    # Add prompt to offer starting the stack with default "y"
    start_stack = False
    if is_interactive:
        start_stack = Confirm.ask("\n[cyan]Do you want to start the Opal stack now?[/cyan]", default=True)
    elif yes:
        # In non-interactive mode with --yes flag, auto-start the stack
        start_stack = True
        console.print("\n[cyan]--yes flag provided. Starting the Opal stack automatically...[/cyan]")
    
    if start_stack:
        console.print("[cyan]Starting the Opal stack...[/cyan]")
        run_docker_compose(["up", "-d"])
        console.print("[green]Opal stack started successfully![/green]")
        
        # Show access information
        strategy = config.get("ssl", {}).get("strategy")
        if strategy == "none":
            console.print(f"\n[bold green]🎉 Opal is now accessible at: http://localhost:{config['opal_http_port']}[/bold green]")
        else:
            hosts = config.get("hosts", ["localhost"])
            port = config.get("opal_external_port", 443)
            console.print(f"\n[bold green]🎉 Opal is now accessible at: https://{hosts[0]}:{port}[/bold green]")
        console.print("[yellow]Default login: administrator / (your chosen password)[/yellow]")