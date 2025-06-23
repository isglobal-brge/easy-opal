from pathlib import Path
from rich.console import Console
import shutil

from src.core.config_manager import NGINX_CONF_DIR, load_config

console = Console()
NGINX_TEMPLATE_PATH = Path("src/templates/nginx.conf.tpl")
MAINTENANCE_PAGE_TEMPLATE_PATH = Path("src/templates/maintenance.html")

def generate_nginx_config():
    """
    Generates the nginx.conf file and copies the maintenance page.
    """
    config = load_config()
    if not NGINX_TEMPLATE_PATH.exists():
        console.print(f"[bold red]NGINX template not found at {NGINX_TEMPLATE_PATH}[/bold red]")
        return

    console.print("[cyan]Generating NGINX configuration...[/cyan]")

    with open(NGINX_TEMPLATE_PATH, "r") as f:
        template = f.read()

    server_names = " ".join(config["hosts"])
    template = template.replace("${OPAL_HOSTNAME}", server_names)

    output_path = NGINX_CONF_DIR / "nginx.conf"
    with open(output_path, "w") as f:
        f.write(template)

    console.print(f"[green]NGINX configuration written to {output_path}[/green]")

    # Also copy the maintenance page to a location accessible by nginx
    html_dir = NGINX_CONF_DIR.parent / "html"
    html_dir.mkdir(exist_ok=True)
    if MAINTENANCE_PAGE_TEMPLATE_PATH.exists():
        shutil.copy(MAINTENANCE_PAGE_TEMPLATE_PATH, html_dir / "maintenance.html")
        console.print(f"[green]Maintenance page copied to {html_dir}[/green]")
    else:
        console.print(f"[bold yellow]Maintenance page template not found at {MAINTENANCE_PAGE_TEMPLATE_PATH}[/bold yellow]") 