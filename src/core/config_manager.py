"""Load and save OpalConfig from/to an instance directory."""

import json

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

    raw = json.loads(ctx.config_path.read_text())
    old_version = raw.get("schema_version", 0)
    raw = migrate_if_needed(raw)
    cfg = OpalConfig.model_validate(raw)

    # Re-save if migration changed the schema version
    if old_version != CURRENT_VERSION:
        save_config(cfg, ctx)

    return cfg


def save_config(config: OpalConfig, ctx: InstanceContext) -> None:
    """Serialize OpalConfig to config.json."""
    ctx.root.mkdir(parents=True, exist_ok=True)
    ctx.config_path.write_text(config.model_dump_json(indent=2) + "\n")
