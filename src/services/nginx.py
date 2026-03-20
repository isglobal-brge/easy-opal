"""NGINX reverse proxy: SSL termination and request routing."""
from src.models.config import OpalConfig
from src.models.enums import SSLStrategy
from src.models.instance import InstanceContext


class NginxService:
    name = "nginx"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.ssl.strategy != SSLStrategy.NONE

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        ports = [f"{config.opal_external_port}:443"]
        if config.ssl.strategy == SSLStrategy.LETSENCRYPT:
            ports.append("80:80")

        volumes = [
            f"{ctx.nginx_conf_dir}/nginx.conf:/etc/nginx/nginx.conf:ro",
            f"{ctx.certs_dir}:/etc/nginx/certs:ro",
            f"{ctx.nginx_html_dir}:/usr/share/nginx/html:ro",
            f"{ctx.letsencrypt_dir / 'www'}:/var/www/certbot:ro",
            f"{ctx.letsencrypt_dir / 'conf'}:/etc/letsencrypt",
        ]

        return {
            "nginx": {
                "image": f"nginx:{config.nginx_version}",
                "container_name": f"{config.stack_name}-nginx",
                "restart": "always",
                "ports": ports,
                "volumes": volumes,
                "depends_on": {"opal": {"condition": "service_healthy"}},
                "healthcheck": {
                    "test": ["CMD-SHELL", "service nginx status || exit 1"],
                    "interval": "10s",
                    "timeout": "3s",
                    "retries": 3,
                    "start_period": "5s",
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
