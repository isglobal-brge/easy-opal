from typing import Literal

from pydantic import BaseModel, Field

from src.models.enums import SSLStrategy, DatabaseType


class SSLConfig(BaseModel):
    strategy: SSLStrategy = SSLStrategy.SELF_SIGNED
    le_email: str = ""


class DatabaseConfig(BaseModel):
    type: DatabaseType
    name: str
    port: int
    user: str = "opal"
    database: str = "opaldata"
    version: str = "latest"


class ProfileConfig(BaseModel):
    name: str
    image: str = "datashield/rock-base"
    tag: str = "latest"


class WatchtowerConfig(BaseModel):
    enabled: bool = False
    poll_interval_hours: int = 24
    cleanup: bool = True


class AgateConfig(BaseModel):
    enabled: bool = False
    version: str = "latest"
    mail_mode: Literal["mailpit", "smtp", "none"] = "none"
    mailpit_port: int = 8025


class MicaConfig(BaseModel):
    enabled: bool = False
    version: str = "latest"
    elasticsearch_version: str = "8.16.1"


class OpalConfig(BaseModel):
    schema_version: int = 2
    stack_name: str = "easy-opal"
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
