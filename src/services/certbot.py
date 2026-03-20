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
                "image": "certbot/certbot",
                "container_name": f"{config.stack_name}-certbot",
                "volumes": [
                    f"{ctx.letsencrypt_dir / 'www'}:/var/www/certbot:rw",
                    f"{ctx.letsencrypt_dir / 'conf'}:/etc/letsencrypt",
                ],
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
