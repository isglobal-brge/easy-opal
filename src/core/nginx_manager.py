from pathlib import Path
from rich.console import Console

from core.config_manager import NGINX_CONF_DIR, load_config

console = Console()
TEMPLATE_PATH = Path("src/templates/nginx.conf.tpl")

def generate_nginx_config():
    """
    Generates the nginx.conf file from the template.
    """
    config = load_config()
    if not TEMPLATE_PATH.exists():
        console.print(f"[bold red]NGINX template not found at {TEMPLATE_PATH}[/bold red]")
        return

    console.print("[cyan]Generating NGINX configuration...[/cyan]")

    with open(TEMPLATE_PATH, "r") as f:
        template = f.read()

    server_names = " ".join(config["hosts"])
    template = template.replace("${OPAL_HOSTNAME}", server_names)

    output_path = NGINX_CONF_DIR / "nginx.conf"
    with open(output_path, "w") as f:
        f.write(template)

    console.print(f"[green]NGINX configuration written to {output_path}[/green]") 