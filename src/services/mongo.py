from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class MongoService:
    name = "mongo"

    def is_enabled(self, config: OpalConfig) -> bool:
        return True

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        return {
            "mongo": {
                "image": f"mongo:{config.mongo_version}",
                "container_name": f"{config.stack_name}-mongo",
                "restart": "always",
                "volumes": [f"{config.stack_name}-mongo-data:/data/db"],
                "healthcheck": {
                    "test": ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 5,
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {f"{config.stack_name}-mongo-data": None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {"MONGO_HOST": "mongo", "MONGO_PORT": "27017"}
