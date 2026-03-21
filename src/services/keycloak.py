"""Keycloak OIDC authentication server: used with Armadillo (opt-in)."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class KeycloakService:
    name = "keycloak"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.keycloak.enabled

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        admin_pw = secrets.get("KEYCLOAK_ADMIN_PASSWORD", "")

        return {
            "keycloak": {
                "image": f"quay.io/keycloak/keycloak:{config.keycloak.version}",
                "container_name": f"{config.stack_name}-keycloak",
                "restart": "always",
                "command": [
                    "start-dev",
                    "--features=scripts",
                    "--health-enabled=true",
                    "--http-management-port=9000",
                    "--hostname=keycloak",
                ],
                "environment": {
                    "KEYCLOAK_ADMIN": config.keycloak.admin_user,
                    "KEYCLOAK_ADMIN_PASSWORD": admin_pw,
                },
                "ports": [f"{config.keycloak.port}:8080"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "bash -c '</dev/tcp/localhost/9000' || exit 1"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 15,
                    "start_period": "30s",
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
