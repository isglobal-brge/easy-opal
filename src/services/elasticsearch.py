"""Elasticsearch service: search index for Mica (opt-in, only with Mica)."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


class ElasticsearchService:
    name = "elasticsearch"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.mica.enabled if hasattr(config, "mica") and config.mica else False

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        return {
            "elasticsearch": {
                "image": f"docker.elastic.co/elasticsearch/elasticsearch:{config.mica.elasticsearch_version}",
                "container_name": f"{config.stack_name}-elasticsearch",
                "restart": "always",
                "environment": {
                    "discovery.type": "single-node",
                    "xpack.security.enabled": "false",
                    "ES_JAVA_OPTS": "-Xms512m -Xmx512m",
                },
                "volumes": [f"{config.stack_name}-elasticsearch-data:/usr/share/elasticsearch/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "curl -sf http://localhost:9200/_cluster/health > /dev/null || exit 1"],
                    "interval": "10s",
                    "timeout": "5s",
                    "retries": 12,
                    "start_period": "30s",
                },
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {f"{config.stack_name}-elasticsearch-data": None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
