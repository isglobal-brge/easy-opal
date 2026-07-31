"""Load and save OpalConfig from/to an instance directory."""

import json
import hashlib
import os
import tempfile
from pathlib import Path

from src.models.config import OpalConfig
from src.models.instance import InstanceContext
from src.core.migration import migrate_if_needed, CURRENT_VERSION


def config_exists(ctx: InstanceContext) -> bool:
    return ctx.config_path.exists()


def load_config(ctx: InstanceContext) -> OpalConfig:
    """Load config.json, auto-migrate if needed, validate via Pydantic."""
    if not ctx.config_path.exists():
        cfg = OpalConfig()
        save_config(cfg, ctx)
        return cfg

    source = ctx.config_path.read_bytes()
    raw = json.loads(source)
    old_version = raw.get("schema_version", 0)
    raw = migrate_if_needed(raw)
    cfg = OpalConfig.model_validate(raw)

    # Re-save if migration changed the schema version
    if old_version != CURRENT_VERSION:
        save_config(cfg, ctx)
    else:
        cfg._source_digest = hashlib.sha256(source).hexdigest()

    return cfg


def ensure_config_unchanged(config: OpalConfig, ctx: InstanceContext) -> None:
    """Reject a stale read-modify-write after another process changed config."""
    expected = config._source_digest
    if expected is None:
        return
    try:
        observed = hashlib.sha256(ctx.config_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError(f"Configuration is no longer readable: {exc}") from exc
    if observed != expected:
        raise RuntimeError(
            "Configuration changed after this command began; retry the command "
            "against the latest settings."
        )


def save_config(config: OpalConfig, ctx: InstanceContext) -> None:
    """Serialize OpalConfig to config.json."""
    ctx.root.mkdir(parents=True, exist_ok=True)
    rendered = (config.model_dump_json(indent=2) + "\n").encode()
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{ctx.config_path.name}.", dir=ctx.root
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, ctx.config_path)
        config._source_digest = hashlib.sha256(rendered).hexdigest()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        Path(temporary).unlink(missing_ok=True)
