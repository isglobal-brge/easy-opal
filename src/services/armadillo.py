"""Armadillo DataSHIELD server: lightweight alternative to Opal."""

import yaml

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


def _generate_application_yml(config: OpalConfig, ctx: InstanceContext) -> None:
    """Generate application.yml for Armadillo with correct Rock profile config."""
    profiles = []
    for i, p in enumerate(config.profiles):
        profiles.append({
            "name": "default" if i == 0 else p.name,
            "image": f"{p.image}:{p.tag}",
            "host": p.name,  # Docker service name
            "port": 8085,
            "package-whitelist": ["dsBase"],
        })

    app_config = {
        "armadillo": {
            "docker-management-enabled": False,
            "docker-run-in-container": True,
            "profiles": profiles,
        },
    }

    config_dir = ctx.data_dir / "armadillo-config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "application.yml").write_text(
        yaml.dump(app_config, default_flow_style=False, sort_keys=False)
    )


class ArmadilloService:
    name = "armadillo"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.flavor == "armadillo"

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        admin_pw = secrets.get("ARMADILLO_ADMIN_PASSWORD", "")

        # Generate application.yml with Rock profile config
        _generate_application_yml(config, ctx)

        env = {
            "SPRING_SECURITY_USER_NAME": "admin",
            "SPRING_SECURITY_USER_PASSWORD": admin_pw,
            "SPRING_SECURITY_USER_ROLES": "SU",
            "SERVER_FORWARD_HEADERS_STRATEGY": "NATIVE",
            "ARMADILLO_CONTAINER_PREFIX": config.stack_name,
        }

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
