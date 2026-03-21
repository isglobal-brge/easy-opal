"""Test schema version migrations."""

import json
import pytest

from src.core.migration import migrate_if_needed, CURRENT_VERSION


class TestMigration:
    def test_v0_to_current(self):
        raw = {
            "stack_name": "old",
            "opal_admin_password": "secret",
            "mongodb": {"enabled": True},
        }
        result = migrate_if_needed(raw)
        assert result["schema_version"] == CURRENT_VERSION
        assert "opal_admin_password" not in result
        assert "mongodb" not in result

    def test_v1_to_current(self):
        raw = {
            "schema_version": 1,
            "ssl": {"strategy": "self-signed", "cert_path": "/x", "key_path": "/y"},
            "watchtower": {"poll_interval": 7200},
        }
        result = migrate_if_needed(raw)
        assert result["schema_version"] == CURRENT_VERSION
        assert "cert_path" not in result["ssl"]
        assert "key_path" not in result["ssl"]
        assert result["watchtower"]["poll_interval_hours"] == 2

    def test_current_version_unchanged(self):
        raw = {"schema_version": CURRENT_VERSION, "stack_name": "test"}
        result = migrate_if_needed(raw)
        assert result == raw

    def test_empty_dict_migrates(self):
        result = migrate_if_needed({})
        assert result["schema_version"] == CURRENT_VERSION

    def test_v1_removes_certbot_version(self):
        raw = {"schema_version": 1, "certbot_version": "latest"}
        result = migrate_if_needed(raw)
        assert "certbot_version" not in result

    def test_v1_watchtower_minimum_1_hour(self):
        raw = {"schema_version": 1, "watchtower": {"poll_interval": 600}}
        result = migrate_if_needed(raw)
        assert result["watchtower"]["poll_interval_hours"] == 1

    def test_migration_persists_on_load(self, tmp_instance):
        """Config should be re-saved after migration."""
        from src.core.config_manager import load_config

        old = {"stack_name": "legacy", "opal_admin_password": "pw"}
        tmp_instance.config_path.write_text(json.dumps(old))

        cfg = load_config(tmp_instance)
        assert cfg.schema_version == CURRENT_VERSION

        # Verify saved to disk
        saved = json.loads(tmp_instance.config_path.read_text())
        assert saved["schema_version"] == CURRENT_VERSION
        assert "opal_admin_password" not in saved
