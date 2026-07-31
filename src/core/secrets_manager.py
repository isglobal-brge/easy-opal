"""Manage secrets.env: generate, load, save, ensure."""

import os
import re
import tempfile
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
_SECRET_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    lines = []
    for key, value in sorted(secrets.items()):
        if not _SECRET_KEY_RE.fullmatch(key):
            raise ValueError(f"Invalid secret key: {key!r}")
        if any(character in value for character in "\r\n\0"):
            raise ValueError(f"Secret {key!r} contains an unsupported newline or NUL.")
        lines.append(f"{key}={value}")
    rendered = ("\n".join(lines) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{ctx.secrets_path.name}.", dir=ctx.root
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, ctx.secrets_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)


def ensure_secrets(ctx: InstanceContext, config: OpalConfig) -> dict[str, str]:
    """Load existing secrets; generate any that are missing."""
    secrets = load_secrets(ctx)
    changed = False

    # Core secrets (Opal flavor)
    if config.flavor == "opal":
        for key in CORE_SECRETS:
            if key not in secrets:
                secrets[key] = generate_password()
                changed = True

    # Armadillo secrets
    if config.flavor == "armadillo":
        if "ARMADILLO_ADMIN_PASSWORD" not in secrets:
            secrets["ARMADILLO_ADMIN_PASSWORD"] = generate_password()
            changed = True

    # Keycloak secret
    if config.keycloak.enabled:
        if "KEYCLOAK_ADMIN_PASSWORD" not in secrets:
            secrets["KEYCLOAK_ADMIN_PASSWORD"] = generate_password()
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
