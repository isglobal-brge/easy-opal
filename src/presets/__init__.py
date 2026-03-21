"""Configuration presets: named templates for common deployment patterns."""

from src.models.config import (
    OpalConfig, SSLConfig, AgateConfig, MicaConfig, WatchtowerConfig,
)
from src.models.enums import SSLStrategy

PRESETS: dict[str, dict] = {
    "opal-dev": {
        "description": "Local development with self-signed SSL",
        "config": {
            "ssl": {"strategy": "self-signed"},
            "watchtower": {"enabled": False},
        },
    },
    "opal-prod": {
        "description": "Production with Let's Encrypt and Watchtower",
        "config": {
            "ssl": {"strategy": "letsencrypt"},
            "watchtower": {"enabled": True, "poll_interval_hours": 24},
        },
    },
    "opal-proxy": {
        "description": "Behind a reverse proxy (no SSL, HTTP only)",
        "config": {
            "ssl": {"strategy": "none"},
        },
    },
    "opal-agate": {
        "description": "Opal + Agate authentication with Mailpit",
        "config": {
            "ssl": {"strategy": "self-signed"},
            "agate": {"enabled": True, "mail_mode": "mailpit"},
        },
    },
    "obiba-full": {
        "description": "Full OBiBa stack: Opal + Agate + Mica + Elasticsearch",
        "config": {
            "ssl": {"strategy": "self-signed"},
            "agate": {"enabled": True, "mail_mode": "mailpit"},
            "mica": {"enabled": True},
        },
    },
    "armadillo-dev": {
        "description": "Armadillo DataSHIELD server for development",
        "config": {
            "flavor": "armadillo",
            "ssl": {"strategy": "self-signed"},
        },
    },
    "armadillo-prod": {
        "description": "Armadillo with Keycloak OIDC for production",
        "config": {
            "flavor": "armadillo",
            "ssl": {"strategy": "letsencrypt"},
            "keycloak": {"enabled": True},
            "watchtower": {"enabled": True, "poll_interval_hours": 24},
        },
    },
}


def get_preset_names() -> list[str]:
    return list(PRESETS.keys())


def get_preset(name: str) -> dict | None:
    return PRESETS.get(name)


def apply_preset(config: OpalConfig, preset_name: str) -> OpalConfig:
    """Apply a preset's values onto an OpalConfig. Returns updated config."""
    preset = PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_name}. Available: {', '.join(PRESETS.keys())}")

    data = config.model_dump()
    _deep_merge(data, preset["config"])
    return OpalConfig.model_validate(data)


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base recursively (in place)."""
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
