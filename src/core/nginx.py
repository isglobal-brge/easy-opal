"""Generate nginx.conf programmatically from config."""

import shutil
from pathlib import Path

from src.models.config import OpalConfig
from src.models.enums import SSLStrategy
from src.models.instance import InstanceContext
from src.utils.console import dim

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _location_block(path: str, upstream: str, port: int, external_port: int = 443) -> str:
    """Generate a location block with proxy and maintenance page fallback."""
    return f"""
        location {path} {{
            proxy_pass http://{upstream}:{port}/;
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-Port {external_port};
            proxy_read_timeout 300s;

            error_page 502 503 504 /maintenance.html;
        }}"""


def _build_https_config(config: OpalConfig, ctx: InstanceContext) -> str:
    """Build full nginx.conf for HTTPS mode."""
    server_names = " ".join(config.hosts)

    # Certificate paths
    if config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        domain = config.hosts[0]
        cert = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        key = f"/etc/letsencrypt/live/{domain}/privkey.pem"
    else:
        cert = "/etc/nginx/certs/opal.crt"
        key = "/etc/nginx/certs/opal.key"

    ext_port = config.opal_external_port

    # Build location blocks for all enabled services
    if config.flavor == "armadillo":
        locations = _location_block("/", "armadillo", 8080, ext_port)
    else:
        locations = _location_block("/", "opal", 8080, ext_port)

    if config.agate.enabled:
        locations += _location_block("/agate/", "agate", 8444, ext_port)

    if config.mica.enabled:
        locations += _location_block("/mica/", "mica", 8445, ext_port)

    return f"""user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {{
    worker_connections 1024;
}}

http {{
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 100m;

    server {{
        listen 80;
        server_name {server_names};

        location /.well-known/acme-challenge/ {{
            root /var/www/certbot;
        }}

        location / {{
            return 301 https://$host$request_uri;
        }}
    }}

    server {{
        listen 443 ssl;
        server_name {server_names};

        # Redirect plain HTTP requests sent to the HTTPS port
        error_page 497 =301 https://$host:{config.opal_external_port}$request_uri;

        ssl_certificate {cert};
        ssl_certificate_key {key};
        ssl_session_cache shared:SSL:10m;
        ssl_session_timeout 10m;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;

        # Maintenance page (auto-refresh)
        location = /maintenance.html {{
            root /usr/share/nginx/html;
            internal;
        }}
{locations}
    }}
}}
"""


def _build_acme_config(config: OpalConfig) -> str:
    """Build HTTP-only config for Let's Encrypt ACME challenge."""
    server_names = " ".join(config.hosts)
    return f"""user nginx;
worker_processes auto;
events {{ worker_connections 1024; }}

http {{
    server {{
        listen 80;
        server_name {server_names};

        location /.well-known/acme-challenge/ {{
            root /var/www/certbot;
        }}

        location / {{
            return 503;
        }}
    }}
}}
"""


def generate_nginx_config(
    config: OpalConfig, ctx: InstanceContext, acme_only: bool = False
) -> None:
    """Generate nginx.conf. No-op if ssl strategy is 'none'."""
    if config.ssl.strategy == SSLStrategy.NONE:
        dim("Skipping NGINX config (no SSL).")
        conf_path = ctx.nginx_conf_dir / "nginx.conf"
        if conf_path.exists():
            conf_path.unlink()
        return

    ctx.nginx_conf_dir.mkdir(parents=True, exist_ok=True)

    if acme_only and config.ssl.strategy == SSLStrategy.LETSENCRYPT:
        content = _build_acme_config(config)
    else:
        content = _build_https_config(config, ctx)

    (ctx.nginx_conf_dir / "nginx.conf").write_text(content)

    # Copy maintenance page
    ctx.nginx_html_dir.mkdir(parents=True, exist_ok=True)
    maintenance_src = TEMPLATES_DIR / "maintenance.html"
    if maintenance_src.exists():
        shutil.copy(maintenance_src, ctx.nginx_html_dir / "maintenance.html")
