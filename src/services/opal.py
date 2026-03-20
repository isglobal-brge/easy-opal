from src.models.config import OpalConfig
from src.models.enums import SSLStrategy
from src.models.instance import InstanceContext


class OpalService:
    name = "opal"

    def is_enabled(self, config: OpalConfig) -> bool:
        return True

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        svc: dict = {
            "image": f"obiba/opal:{config.opal_version}",
            "container_name": f"{config.stack_name}-opal",
            "restart": "always",
            "depends_on": {"mongo": {"condition": "service_healthy"}},
            "volumes": ["opal_srv_data:/srv"],
            "env_file": [str(ctx.secrets_path)],
            "healthcheck": {
                "test": ["CMD-SHELL", "bash -c '</dev/tcp/localhost/8080' || exit 1"],
                "interval": "15s",
                "timeout": "5s",
                "retries": 20,
                "start_period": "60s",
            },
            "environment": {},  # Populated by ServiceRegistry from all modules
        }

        # In 'none' mode, expose Opal directly on HTTP
        if config.ssl.strategy == SSLStrategy.NONE:
            svc["ports"] = [f"{config.opal_http_port}:8080"]

        return {"opal": svc}

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {"opal_srv_data": None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        env = {
            "OPAL_ADMINISTRATOR_PASSWORD": secrets.get("OPAL_ADMIN_PASSWORD", ""),
            "ROCK_DEFAULT_ADMINISTRATOR_USERNAME": "administrator",
            "ROCK_DEFAULT_ADMINISTRATOR_PASSWORD": secrets.get("ROCK_ADMINISTRATOR_PASSWORD", ""),
            "ROCK_DEFAULT_MANAGER_USERNAME": "manager",
            "ROCK_DEFAULT_MANAGER_PASSWORD": secrets.get("ROCK_MANAGER_PASSWORD", ""),
            "ROCK_DEFAULT_USER_USERNAME": "user",
            "ROCK_DEFAULT_USER_PASSWORD": secrets.get("ROCK_USER_PASSWORD", ""),
        }

        # CSRF: computed from hosts with port (browser sends Origin with port)
        if config.hosts:
            csrf_origins = []
            for h in config.hosts:
                if config.ssl.strategy == SSLStrategy.NONE:
                    csrf_origins.append(f"http://{h}:{config.opal_http_port}")
                else:
                    csrf_origins.append(f"https://{h}:{config.opal_external_port}")
                    # Also allow without port for standard ports
                    if config.opal_external_port == 443:
                        csrf_origins.append(f"https://{h}")
            env["CSRF_ALLOWED"] = ",".join(csrf_origins)
        else:
            env["CSRF_ALLOWED"] = "*"

        # Proxy settings
        if config.ssl.strategy == SSLStrategy.NONE:
            env["OPAL_PROXY_SECURE"] = "false"
            env["OPAL_PROXY_HOST"] = "localhost"
            env["OPAL_PROXY_PORT"] = str(config.opal_http_port)
        else:
            env["OPAL_PROXY_SECURE"] = "true"
            env["OPAL_PROXY_HOST"] = config.hosts[0] if config.hosts else "localhost"
            env["OPAL_PROXY_PORT"] = str(config.opal_external_port)

        return env
