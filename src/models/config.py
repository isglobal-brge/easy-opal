from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from src.models.enums import SSLStrategy, DatabaseType


SAFE_RESOURCE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"


class SSLConfig(BaseModel):
    strategy: SSLStrategy = SSLStrategy.SELF_SIGNED
    le_email: str = ""


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    type: DatabaseType
    name: str = Field(pattern=SAFE_RESOURCE_NAME_PATTERN)
    port: int
    user: str = "opal"
    database: str = "opaldata"
    version: str = "latest"
    external: bool = False
    host: str = ""


class ProfileConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    name: str = Field(pattern=SAFE_RESOURCE_NAME_PATTERN)
    image: str = "datashield/rock-base"
    tag: str = "latest"


class WatchtowerConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    enabled: bool = False
    poll_interval_hours: int = Field(default=24, ge=1)
    cleanup: bool = True


class SmtpConfig(BaseModel):
    host: str = ""
    port: int = 587
    user: str = ""
    tls: bool = True
    auth: bool = True
    from_address: str = "opal@example.org"


class AgateConfig(BaseModel):
    enabled: bool = False
    version: str = "latest"
    mail_mode: Literal["mailpit", "smtp", "none"] = "none"
    mailpit_port: int = 8025
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)


class MicaConfig(BaseModel):
    enabled: bool = False
    version: str = "latest"
    elasticsearch_version: str = "8.16.1"


class KeycloakConfig(BaseModel):
    enabled: bool = False
    version: str = "25.0.6"
    admin_user: str = "admin"
    port: int = 8080


class BackupConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1)
    keep: int = Field(default=7, ge=0)


class ProfileUpdaterConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1)


class ArmadilloConfig(BaseModel):
    version: str = "latest"
    port: int = 8080
    auth_mode: Literal["local", "oidc"] = "local"


class OpalConfig(BaseModel):
    model_config = ConfigDict(validate_assignment=True)
    _source_digest: str | None = PrivateAttr(default=None)

    schema_version: int = 2
    flavor: Literal["opal", "armadillo"] = "opal"
    stack_name: str = Field(
        default="easy-opal", pattern=SAFE_RESOURCE_NAME_PATTERN
    )
    hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    opal_version: str = "latest"
    mongo_version: str = "latest"
    nginx_version: str = "latest"
    opal_external_port: int = 443
    opal_http_port: int = 8080
    ssl: SSLConfig = Field(default_factory=SSLConfig)
    profiles: list[ProfileConfig] = Field(
        default_factory=lambda: [ProfileConfig(name="rock")]
    )
    databases: list[DatabaseConfig] = Field(default_factory=list)
    watchtower: WatchtowerConfig = Field(default_factory=WatchtowerConfig)
    agate: AgateConfig = Field(default_factory=AgateConfig)
    mica: MicaConfig = Field(default_factory=MicaConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    profile_updater: ProfileUpdaterConfig = Field(default_factory=ProfileUpdaterConfig)
    armadillo: ArmadilloConfig = Field(default_factory=ArmadilloConfig)
    keycloak: KeycloakConfig = Field(default_factory=KeycloakConfig)
