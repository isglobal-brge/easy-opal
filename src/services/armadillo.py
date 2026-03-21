"""Armadillo DataSHIELD server: lightweight alternative to Opal."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class ArmadilloService:
    name = "armadillo"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.flavor == "armadillo"

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        admin_pw = secrets.get("ARMADILLO_ADMIN_PASSWORD", "")

        env = {
            "SPRING_SECURITY_USER_NAME": "admin",
            "SPRING_SECURITY_USER_PASSWORD": admin_pw,
            "ARMADILLO_DOCKER_MANAGEMENT_ENABLED": "false",
            "ARMADILLO_DOCKER_RUN_IN_CONTAINER": "true",
            "ARMADILLO_CONTAINER_PREFIX": config.stack_name,
        }

        # R server URLs (all profiles)
        for i, p in enumerate(config.profiles):
            port = 6311 if "rock-base" in p.image else 8085
            env[f"SPRING_RSERVER_URL"] = f"http://{p.name}:{port}"

        volumes = [
            f"{config.stack_name}-armadillo-data:/data",
            f"{ctx.data_dir / 'armadillo-config'}:/config",
        ]

        svc: dict = {
            "image": f"molgenis/molgenis-armadillo:{config.armadillo.version}",
            "container_name": f"{config.stack_name}-armadillo",
            "platform": "linux/amd64",
            "restart": "always",
            "volumes": volumes,
            "environment": env,
            "healthcheck": {
                "test": ["CMD-SHELL", "wget -qO- http://localhost:8080/actuator/health || exit 1"],
                "interval": "15s",
                "timeout": "5s",
                "retries": 20,
                "start_period": "60s",
            },
        }

        # In 'none' SSL mode, expose directly
        if config.ssl.strategy == "none":
            svc["ports"] = [f"{config.opal_http_port}:8080"]

        # Depend on keycloak if OIDC
        if config.keycloak.enabled:
            svc["depends_on"] = {"keycloak": {"condition": "service_healthy"}}

        return {"armadillo": svc}

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {f"{config.stack_name}-armadillo-data": None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
