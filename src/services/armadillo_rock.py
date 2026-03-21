"""Rock R server for Armadillo: simplified, no Opal dependencies."""

from src.models.config import OpalConfig, ProfileConfig
from src.models.instance import InstanceContext


class ArmadilloRockService:
    def __init__(self, profile: ProfileConfig):
        self.profile = profile
        self.name = f"armadillo-rock-{profile.name}"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.flavor == "armadillo"

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        p = self.profile
        port = 8085  # All Rock images use port 8085
        volume_name = f"{config.stack_name}-{p.name}-data"

        return {
            p.name: {
                "image": f"{p.image}:{p.tag}",
                "container_name": f"{config.stack_name}-{p.name}",
                "platform": "linux/amd64",
                "restart": "always",
                "volumes": [f"{volume_name}:/srv"],
                "healthcheck": {
                    "test": ["CMD-SHELL", f"bash -c '</dev/tcp/localhost/{port}' || exit 1"],
                    "interval": "15s",
                    "timeout": "5s",
                    "retries": 20,
                    "start_period": "30s",
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {f"{config.stack_name}-{self.profile.name}-data": None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
