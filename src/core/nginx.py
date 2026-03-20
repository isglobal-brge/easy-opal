"""Generate nginx.conf from config and templates."""

import shutil
from pathlib import Path

from src.models.config import OpalConfig
from src.models.enums import SSLStrategy
from src.models.instance import InstanceContext
from src.utils.console import dim

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def generate_nginx_config(
    config: OpalConfig, ctx: InstanceContext, acme_only: bool = False
) -> None:
    """Generate nginx.conf. No-op if ssl strategy is 'none'."""
    if config.ssl.strategy == SSLStrategy.NONE:
        dim("Skipping NGINX config (no SSL).")
        # Clean up any old config
        conf_path = ctx.nginx_conf_dir / "nginx.conf"
        if conf_path.exists():
            conf_path.unlink()
        return

    ctx.nginx_conf_dir.mkdir(parents=True, exist_ok=True)

    # Choose template
    if acme_only and config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        template_name = "nginx_acme.conf.tpl"
    else:
        template_name = "nginx_https.conf.tpl"

    template_path = TEMPLATES_DIR / template_name
    # Fallback to old names
    if not template_path.exists():
        alt = {"nginx_https.conf.tpl": "nginx.conf.tpl", "nginx_acme.conf.tpl": "nginx-acme.conf.tpl"}
        template_path = TEMPLATES_DIR / alt.get(template_name, template_name)

    if not template_path.exists():
        raise FileNotFoundError(f"NGINX template not found: {template_path}")

    template = template_path.read_text()

    # Substitute placeholders
    server_names = " ".join(config.hosts)
    template = template.replace("${OPAL_HOSTNAME}", server_names)

    # Certificate paths (container-internal)
    if config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        domain = config.hosts[0]
        cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        key_path = f"/etc/letsencrypt/live/{domain}/privkey.pem"
    else:
        cert_path = "/etc/nginx/certs/opal.crt"
        key_path = "/etc/nginx/certs/opal.key"

    template = template.replace("/etc/nginx/certs/opal.crt", cert_path)
    template = template.replace("/etc/nginx/certs/opal.key", key_path)

    (ctx.nginx_conf_dir / "nginx.conf").write_text(template)

    # Copy maintenance page
    ctx.nginx_html_dir.mkdir(parents=True, exist_ok=True)
    maintenance_src = TEMPLATES_DIR / "maintenance.html"
    if maintenance_src.exists():
        shutil.copy(maintenance_src, ctx.nginx_html_dir / "maintenance.html")
