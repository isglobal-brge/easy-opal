"""Test Pydantic models: validation, serialization, defaults."""

import json
import pytest
from pydantic import ValidationError

from src.models.config import (
    BackupConfig,
    DatabaseConfig,
    OpalConfig,
    ProfileConfig,
    ProfileUpdaterConfig,
    SSLConfig,
    WatchtowerConfig,
)
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

    @pytest.mark.parametrize(
        ("model", "field", "value"),
        [
            (OpalConfig, "stack_name", "../escape"),
            (OpalConfig, "stack_name", "study/name"),
            (OpalConfig, "stack_name", "study name"),
            (DatabaseConfig, "name", "../database"),
            (DatabaseConfig, "name", "database$name"),
            (ProfileConfig, "name", "profile/name"),
            (ProfileConfig, "name", ""),
        ],
    )
    def test_resource_names_reject_unsafe_values(self, model, field, value):
        values = {field: value}
        if model is DatabaseConfig:
            values.update(type="postgres", port=5432)
        with pytest.raises(ValidationError):
            model(**values)

    @pytest.mark.parametrize("name", ["study", "My.Opal", "db_name-1"])
    def test_resource_names_preserve_safe_historical_values(self, name):
        assert OpalConfig(stack_name=name).stack_name == name
        assert ProfileConfig(name=name).name == name
        assert DatabaseConfig(type="postgres", name=name, port=5432).name == name

    def test_resource_name_assignment_is_validated(self):
        config = OpalConfig(stack_name="study")
        profile = ProfileConfig(name="rock")

        with pytest.raises(ValidationError):
            config.stack_name = "../escape"
        with pytest.raises(ValidationError):
            profile.name = "../escape"

    @pytest.mark.parametrize(
        ("model", "values"),
        [
            (WatchtowerConfig, {"poll_interval_hours": 0}),
            (BackupConfig, {"interval_hours": 0}),
            (BackupConfig, {"keep": -1}),
            (ProfileUpdaterConfig, {"interval_hours": 0}),
        ],
    )
    def test_scheduled_job_limits_reject_values_below_minimum(
        self, model, values
    ):
        with pytest.raises(ValidationError):
            model(**values)

    def test_scheduled_job_limits_accept_minimum_values(self):
        assert WatchtowerConfig(poll_interval_hours=1).poll_interval_hours == 1
        backup = BackupConfig(interval_hours=1, keep=0)
        assert (backup.interval_hours, backup.keep) == (1, 0)
        assert ProfileUpdaterConfig(interval_hours=1).interval_hours == 1

    @pytest.mark.parametrize(
        ("config", "field", "value"),
        [
            (WatchtowerConfig(), "poll_interval_hours", 0),
            (BackupConfig(), "interval_hours", 0),
            (BackupConfig(), "keep", -1),
            (ProfileUpdaterConfig(), "interval_hours", 0),
        ],
    )
    def test_scheduled_job_assignment_revalidates_limits(
        self, config, field, value
    ):
        with pytest.raises(ValidationError):
            setattr(config, field, value)


class TestSSLConfig:
    def test_defaults(self):
        ssl = SSLConfig()
        assert ssl.strategy == SSLStrategy.SELF_SIGNED
        assert ssl.le_email == ""

    def test_none_strategy(self):
        ssl = SSLConfig(strategy=SSLStrategy.NONE)
        assert ssl.strategy == SSLStrategy.NONE
