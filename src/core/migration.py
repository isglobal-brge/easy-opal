"""Schema version migrations for config.json."""

CURRENT_VERSION = 2


def migrate_if_needed(raw: dict) -> dict:
    """Apply migrations from the raw dict's schema_version up to CURRENT_VERSION."""
    version = raw.get("schema_version", 0)

    migrations = {
        0: _migrate_v0_to_v1,
        1: _migrate_v1_to_v2,
    }

    while version < CURRENT_VERSION:
        fn = migrations.get(version)
        if fn is None:
            break
        raw = fn(raw)
        version = raw.get("schema_version", version + 1)

    return raw


def _migrate_v0_to_v1(raw: dict) -> dict:
    """Legacy config with no schema_version. Normalize to v1."""
    raw["schema_version"] = 1
    raw.pop("opal_admin_password", None)
    raw.pop("mongodb", None)
    return raw


def _migrate_v1_to_v2(raw: dict) -> dict:
    """v1 -> v2: Remove stored cert paths, convert watchtower interval to hours."""
    raw["schema_version"] = 2

    ssl = raw.get("ssl", {})
    ssl.pop("cert_path", None)
    ssl.pop("key_path", None)

    wt = raw.get("watchtower", {})
    if "poll_interval" in wt:
        seconds = wt.pop("poll_interval")
        wt["poll_interval_hours"] = max(1, seconds // 3600)

    raw.pop("opal_admin_password", None)
    raw.pop("certbot_version", None)

    return raw
