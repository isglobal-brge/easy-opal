"""Mica data portal: metadata catalog and search (opt-in, requires Agate)."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class MicaService:
    name = "mica"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.mica.enabled if hasattr(config, "mica") and config.mica else False

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        mica_pw = secrets.get("MICA_ADMIN_PASSWORD", "")

        return {
            "mica": {
                "image": f"obiba/mica:{config.mica.version}",
                "container_name": f"{config.stack_name}-mica",
                "restart": "always",
                "depends_on": {
                    "mongo": {"condition": "service_healthy"},
                    "elasticsearch": {"condition": "service_healthy"},
                },
                "environment": {
                    "MICA_ADMINISTRATOR_PASSWORD": mica_pw,
                    "MONGO_HOST": "mongo",
                    "MONGO_PORT": "27017",
                    "AGATE_URL": "https://agate:8444",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "bash -c '</dev/tcp/localhost/8445' || exit 1"],
                    "interval": "15s",
                    "timeout": "5s",
                    "retries": 20,
                    "start_period": "60s",
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
