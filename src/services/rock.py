"""Rock R server profiles: one service instance per configured profile."""
from src.models.config import OpalConfig, ProfileConfig
from src.models.instance import InstanceContext


class RockService:
    def __init__(self, profile: ProfileConfig):
        self.profile = profile
        self.name = f"rock-{profile.name}"

    def is_enabled(self, config: OpalConfig) -> bool:
        return True

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        p = self.profile
        service_name = p.name
        cluster = "default" if service_name == "rock" else service_name
        volume_name = f"{config.stack_name}-{service_name}-data"

        rock_admin_pw = secrets["ROCK_ADMINISTRATOR_PASSWORD"]
        rock_manager_pw = secrets["ROCK_MANAGER_PASSWORD"]
        rock_user_pw = secrets["ROCK_USER_PASSWORD"]

        return {
            service_name: {
                "image": f"{p.image}:{p.tag}",
                "container_name": f"{config.stack_name}-{service_name}",
                "restart": "always",
                "environment": {
                    "ROCK_CLUSTER": cluster,
                    "ROCK_ID": f"{config.stack_name}-{service_name}",
                    "ROCK_ADMINISTRATOR_NAME": "administrator",
                    "ROCK_ADMINISTRATOR_PASSWORD": rock_admin_pw,
                    "ROCK_MANAGER_NAME": "manager",
                    "ROCK_MANAGER_PASSWORD": rock_manager_pw,
                    "ROCK_USER_NAME": "user",
                    "ROCK_USER_PASSWORD": rock_user_pw,
                },
                "volumes": [f"{volume_name}:/srv"],
                "depends_on": {"opal": {"condition": "service_healthy"}},
                "healthcheck": {
                    "test": ["CMD-SHELL", "bash -c '</dev/tcp/localhost/8085' || exit 1"],
                    "interval": "15s",
                    "timeout": "5s",
                    "retries": 20,
                    "start_period": "30s",
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        volume_name = f"{config.stack_name}-{self.profile.name}-data"
        return {volume_name: None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        # ROCK_HOSTS is set once by the first Rock profile, but we need all of them
        # The registry aggregates env vars, so last write wins for ROCK_HOSTS.
        # We compute the full list here — all profiles contribute to one variable.
        hosts = [f"http://{p.name}:8085" for p in config.profiles]
        return {"ROCK_HOSTS": ",".join(hosts)}
