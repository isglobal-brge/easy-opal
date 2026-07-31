"""Service module registry. Each service contributes its compose fragment."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


@runtime_checkable
class ServiceModule(Protocol):
    name: str

    def is_enabled(self, config: OpalConfig) -> bool: ...
    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict[str, Any]: ...
    def compose_volumes(self, config: OpalConfig) -> dict[str, Any]: ...
    def opal_env_vars(
        self, config: OpalConfig, secrets: dict[str, str]
    ) -> dict[str, str]: ...


class ServiceRegistry:
    """Assembles a complete Compose dict from registered service modules."""

    def __init__(
        self,
        config: OpalConfig,
        ctx: InstanceContext,
        secrets: dict[str, str],
        *,
        runtime_name: str = "docker",
    ):
        if runtime_name not in {"docker", "podman"}:
            raise ValueError(
                f"Unsupported container runtime '{runtime_name}'. "
                "Expected 'docker' or 'podman'."
            )
        self.config = config
        self.ctx = ctx
        self.secrets = secrets
        self.runtime_name = runtime_name
        self._modules: list[ServiceModule] = []
        self._register_all()

    def _register_all(self) -> None:
        from src.services.nginx import NginxService
        from src.services.certbot import CertbotService

        # Common services (both flavors)
        candidates: list[ServiceModule] = [
            NginxService(),
            CertbotService(),
        ]

        if self.config.flavor == "opal":
            from src.services.mongo import MongoService
            from src.services.opal import OpalService
            from src.services.rock import RockService
            from src.services.database import DatabaseService
            from src.services.agate import AgateService
            from src.services.mailpit import MailpitService
            from src.services.mica import MicaService
            from src.services.elasticsearch import ElasticsearchService

            candidates.extend([
                MongoService(),
                OpalService(),
                AgateService(),
                MailpitService(),
                MicaService(),
                ElasticsearchService(),
            ])
            for profile in self.config.profiles:
                candidates.append(RockService(profile))
            for db in self.config.databases:
                candidates.append(DatabaseService(db))

        elif self.config.flavor == "armadillo":
            from src.services.armadillo import ArmadilloService
            from src.services.armadillo_rock import ArmadilloRockService
            from src.services.keycloak import KeycloakService

            candidates.extend([
                ArmadilloService(),
                KeycloakService(),
            ])
            for profile in self.config.profiles:
                candidates.append(ArmadilloRockService(profile))

        self._modules = [m for m in candidates if m.is_enabled(self.config)]

    def validate_runtime_support(self) -> None:
        if self.config.flavor == "armadillo" and self.config.databases:
            raise ValueError(
                "Additional PostgreSQL, MySQL, and MariaDB services are only "
                "supported for the Opal flavor. Remove the database configuration "
                "or select --flavor opal."
            )
        unsupported = [
            mod.name
            for mod in self._modules
            if self.runtime_name
            not in getattr(mod, "supported_runtimes", {"docker", "podman"})
        ]
        if unsupported:
            features = ", ".join(unsupported)
            raise ValueError(
                f"Runtime '{self.runtime_name}' does not support these enabled "
                f"features: {features}. Disable them before retrying or select "
                "a supported runtime."
            )

    def assemble_compose(self) -> dict[str, Any]:
        """Merge all enabled service fragments into a complete compose dict."""
        # Validate every module before compose generation, since some modules
        # write helper scripts as part of generating their fragment.
        self.validate_runtime_support()

        services: dict[str, Any] = {}
        volumes: dict[str, Any] = {}

        # Collect opal env vars from all modules
        opal_env: dict[str, str] = {}
        for mod in self._modules:
            opal_env.update(mod.opal_env_vars(self.config, self.secrets))

        for mod in self._modules:
            svc = mod.compose_services(self.config, self.ctx, self.secrets)
            # Inject aggregated env vars into the opal service
            if "opal" in svc and opal_env:
                svc["opal"]["environment"] = opal_env
            services.update(svc)
            volumes.update(mod.compose_volumes(self.config))

        return {"services": services, "volumes": volumes}
