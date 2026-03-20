"""Test Pydantic models: validation, serialization, defaults."""

import json
import pytest
from src.models.config import OpalConfig, SSLConfig, DatabaseConfig, ProfileConfig
from src.models.enums import SSLStrategy, DatabaseType


class TestOpalConfig:
    def test_defaults(self):
        cfg = OpalConfig()
        assert cfg.schema_version == 2
        assert cfg.stack_name == "easy-opal"
        assert cfg.ssl.strategy == SSLStrategy.SELF_SIGNED
        assert cfg.opal_version == "latest"
        assert len(cfg.profiles) == 1
        assert cfg.profiles[0].name == "rock"

    def test_round_trip(self):
        cfg = OpalConfig()
        data = json.loads(cfg.model_dump_json())
        cfg2 = OpalConfig.model_validate(data)
        assert cfg == cfg2

    def test_custom_config(self):
        cfg = OpalConfig(
            stack_name="prod",
            hosts=["opal.example.com"],
            ssl=SSLConfig(strategy=SSLStrategy.LETSENCRYPT, le_email="a@b.com"),
            databases=[DatabaseConfig(type=DatabaseType.POSTGRES, name="db1", port=5432)],
            watchtower={"enabled": True, "poll_interval_hours": 6},
        )
        assert cfg.ssl.strategy == SSLStrategy.LETSENCRYPT
        assert cfg.databases[0].type == DatabaseType.POSTGRES
        assert cfg.watchtower.poll_interval_hours == 6

    def test_invalid_strategy_rejected(self):
        with pytest.raises(Exception):
            OpalConfig(ssl={"strategy": "invalid"})

    def test_invalid_db_type_rejected(self):
        with pytest.raises(Exception):
            DatabaseConfig(type="oracle", name="x", port=1521)


class TestSSLConfig:
    def test_defaults(self):
        ssl = SSLConfig()
        assert ssl.strategy == SSLStrategy.SELF_SIGNED
        assert ssl.le_email == ""

    def test_none_strategy(self):
        ssl = SSLConfig(strategy=SSLStrategy.NONE)
        assert ssl.strategy == SSLStrategy.NONE
