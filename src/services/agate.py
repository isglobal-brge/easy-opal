"""Agate authentication server: user management and notifications (opt-in)."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class AgateService:
    name = "agate"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.agate.enabled if hasattr(config, "agate") and config.agate else False

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        agate_pw = secrets.get("AGATE_ADMIN_PASSWORD", "")

        svc: dict = {
            "image": f"obiba/agate:{config.agate.version}",
            "container_name": f"{config.stack_name}-agate",
            "restart": "always",
            "depends_on": {"mongo": {"condition": "service_healthy"}},
            "environment": {
                "AGATE_ADMINISTRATOR_PASSWORD": agate_pw,
                "MONGO_HOST": "mongo",
                "MONGO_PORT": "27017",
            },
            "healthcheck": {
                "test": ["CMD-SHELL", "bash -c '</dev/tcp/localhost/8444' || exit 1"],
                "interval": "15s",
                "timeout": "5s",
                "retries": 20,
                "start_period": "30s",
            },
        }

        # Mail configuration
        if config.agate.mail_mode == "mailpit":
            svc["environment"]["MAIL_HOST"] = "mailpit"
            svc["environment"]["MAIL_PORT"] = "1025"

        return {"agate": svc}

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {"AGATE_URL": "https://agate:8444"}
