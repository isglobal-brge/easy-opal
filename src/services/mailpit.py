"""Mailpit dev mail server: captures emails for Agate notifications (opt-in)."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class MailpitService:
    name = "mailpit"

    def is_enabled(self, config: OpalConfig) -> bool:
        if not hasattr(config, "agate") or not config.agate:
            return False
        return config.agate.enabled and config.agate.mail_mode == "mailpit"

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        return {
            "mailpit": {
                "image": "axllent/mailpit:latest",
                "container_name": f"{config.stack_name}-mailpit",
                "restart": "always",
                "ports": [
                    f"{config.agate.mailpit_port}:8025",
                ],
                "healthcheck": {
                    "test": ["CMD-SHELL", "wget -qO- http://localhost:8025/ > /dev/null || exit 1"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 3,
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
