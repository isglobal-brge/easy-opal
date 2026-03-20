from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class WatchtowerService:
    name = "watchtower"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.watchtower.enabled

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        env: dict[str, str] = {
            "WATCHTOWER_POLL_INTERVAL": str(config.watchtower.poll_interval_hours * 3600),
        }
        if config.watchtower.cleanup:
            env["WATCHTOWER_CLEANUP"] = "true"

        return {
            "watchtower": {
                "image": "containrrr/watchtower",
                "container_name": f"{config.stack_name}-watchtower",
                "restart": "always",
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "environment": env,
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
