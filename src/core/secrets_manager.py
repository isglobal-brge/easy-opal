"""Manage secrets.env: generate, load, save, ensure."""

import os
from pathlib import Path

from src.models.config import OpalConfig
from src.models.instance import InstanceContext
from src.utils.crypto import generate_password

# Secrets that always exist
CORE_SECRETS = [
    "OPAL_ADMIN_PASSWORD",
    "ROCK_ADMINISTRATOR_PASSWORD",
    "ROCK_MANAGER_PASSWORD",
    "ROCK_USER_PASSWORD",
]


def load_secrets(ctx: InstanceContext) -> dict[str, str]:
    """Parse secrets.env into a dict. Returns empty dict if missing."""
    if not ctx.secrets_path.exists():
        return {}
    secrets = {}
    for line in ctx.secrets_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        secrets[key.strip()] = value.strip()
    return secrets


def save_secrets(secrets: dict[str, str], ctx: InstanceContext) -> None:
    """Write dict as KEY=VALUE lines to secrets.env with strict permissions."""
    ctx.root.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(secrets.items())]
    ctx.secrets_path.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(ctx.secrets_path, 0o600)
    except OSError as e:
        from src.utils.console import warning
        warning(f"Could not set permissions on {ctx.secrets_path}: {e}")


def ensure_secrets(ctx: InstanceContext, config: OpalConfig) -> dict[str, str]:
    """Load existing secrets; generate any that are missing."""
    secrets = load_secrets(ctx)
    changed = False

    # Core secrets
    for key in CORE_SECRETS:
        if key not in secrets:
            secrets[key] = generate_password()
            changed = True

    # Agate secrets
    if hasattr(config, "agate") and config.agate and config.agate.enabled:
        if "AGATE_ADMIN_PASSWORD" not in secrets:
            secrets["AGATE_ADMIN_PASSWORD"] = generate_password()
            changed = True
        # SMTP password placeholder (user must set it for real SMTP)
        if config.agate.mail_mode == "smtp" and "SMTP_PASSWORD" not in secrets:
            secrets["SMTP_PASSWORD"] = ""
            changed = True

    # Mica secret
    if hasattr(config, "mica") and config.mica and config.mica.enabled:
        if "MICA_ADMIN_PASSWORD" not in secrets:
            secrets["MICA_ADMIN_PASSWORD"] = generate_password()
            changed = True

    # Per-database secrets
    for db in config.databases:
        key = f"{db.name.upper().replace('-', '_')}_PASSWORD"
        if key not in secrets:
            secrets[key] = generate_password()
            changed = True

    if changed:
        save_secrets(secrets, ctx)

    return secrets
