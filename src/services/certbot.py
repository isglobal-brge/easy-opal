"""Certbot service: Let's Encrypt certificate acquisition and renewal."""
from src.models.config import OpalConfig
from src.models.enums import SSLStrategy
from src.models.instance import InstanceContext


class CertbotService:
    name = "certbot"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.ssl.strategy == SSLStrategy.LETSENCRYPT

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        return {
            "certbot": {
                "image": "docker.io/certbot/certbot",
                "container_name": f"{config.stack_name}-certbot",
                # Available to `compose run certbot`, but excluded from a
                # normal `compose up` because it is a one-shot CLI image.
                "profiles": ["certbot"],
                "volumes": [
                    f"{ctx.letsencrypt_dir / 'www'}:/var/www/certbot:rw,z",
                    f"{ctx.letsencrypt_dir / 'conf'}:/etc/letsencrypt:rw,z",
                ],
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
