"""Test core modules: config, secrets, instances, ssl, network."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from src.models.config import OpalConfig, DatabaseConfig
from src.models.instance import InstanceContext
from src.core.config_manager import load_config, save_config, config_exists
from src.core.secrets_manager import load_secrets, save_secrets, ensure_secrets
from src.core.ssl import generate_server_cert, ensure_ca, get_cert_info
from src.utils.network import validate_port, is_port_in_use, find_free_port
from src.utils.crypto import generate_password


class TestConfigManager:
    def test_creates_default_on_first_load(self, tmp_instance):
        assert not config_exists(tmp_instance)
        cfg = load_config(tmp_instance)
        assert cfg.schema_version == 2
        assert config_exists(tmp_instance)

    def test_save_and_load_round_trip(self, tmp_instance):
        cfg = OpalConfig(stack_name="test-stack", hosts=["opal.dev"])
        save_config(cfg, tmp_instance)
        loaded = load_config(tmp_instance)
        assert loaded.stack_name == "test-stack"
        assert loaded.hosts == ["opal.dev"]

    def test_load_invalid_json_raises(self, tmp_instance):
        tmp_instance.config_path.write_text("not json!")
        with pytest.raises(Exception):
            load_config(tmp_instance)


class TestSecretsManager:
    def test_ensure_generates_all_core_secrets(self, tmp_instance):
        cfg = OpalConfig()
        secrets = ensure_secrets(tmp_instance, cfg)
        assert "OPAL_ADMIN_PASSWORD" in secrets
        assert "ROCK_ADMINISTRATOR_PASSWORD" in secrets
        assert "ROCK_MANAGER_PASSWORD" in secrets
        assert "ROCK_USER_PASSWORD" in secrets
        assert all(len(v) > 20 for v in secrets.values())

    def test_ensure_generates_db_secrets(self, tmp_instance):
        cfg = OpalConfig(databases=[
            DatabaseConfig(type="postgres", name="analytics", port=5432),
        ])
        secrets = ensure_secrets(tmp_instance, cfg)
        assert "ANALYTICS_PASSWORD" in secrets

    def test_secrets_persist(self, tmp_instance):
        secrets = {"KEY": "value123"}
        save_secrets(secrets, tmp_instance)
        loaded = load_secrets(tmp_instance)
        assert loaded == secrets

    def test_secrets_file_permissions(self, tmp_instance):
        secrets = {"KEY": "val"}
        save_secrets(secrets, tmp_instance)
        mode = os.stat(tmp_instance.secrets_path).st_mode & 0o777
        assert mode == 0o600

    def test_empty_secrets_returns_empty_dict(self, tmp_instance):
        assert load_secrets(tmp_instance) == {}

    def test_ensure_idempotent(self, tmp_instance):
        cfg = OpalConfig()
        s1 = ensure_secrets(tmp_instance, cfg)
        s2 = ensure_secrets(tmp_instance, cfg)
        assert s1 == s2  # Same passwords on second call


class TestSSL:
    def test_ca_persistent(self, tmp_instance):
        ca1_key, ca1_cert = ensure_ca(tmp_instance)
        ca2_key, ca2_cert = ensure_ca(tmp_instance)
        assert ca1_cert.serial_number == ca2_cert.serial_number

    def test_server_cert_has_sans(self, tmp_instance):
        cfg = OpalConfig(hosts=["localhost", "10.0.0.1", "opal.dev"])
        save_config(cfg, tmp_instance)
        generate_server_cert(tmp_instance, cfg)
        info = get_cert_info(tmp_instance)
        assert "localhost" in info["dns_names"]
        assert "opal.dev" in info["dns_names"]
        assert "10.0.0.1" in info["ip_addresses"]

    def test_key_permissions(self, tmp_instance):
        cfg = OpalConfig(hosts=["localhost"])
        save_config(cfg, tmp_instance)
        generate_server_cert(tmp_instance, cfg)
        for f in ["opal.key", "ca.key"]:
            mode = os.stat(tmp_instance.certs_dir / f).st_mode & 0o777
            assert mode == 0o600, f"{f}: expected 0o600, got {oct(mode)}"

    def test_no_cert_returns_none(self, tmp_instance):
        assert get_cert_info(tmp_instance) is None


class TestNetwork:
    def test_validate_port_valid(self):
        assert validate_port(80) is None
        assert validate_port(443) is None
        assert validate_port(8080) is None
        assert validate_port(65535) is None

    def test_validate_port_invalid(self):
        assert validate_port(0) is not None
        assert validate_port(-1) is not None
        assert validate_port(70000) is not None

    def test_find_free_port_skips_reserved(self):
        port = find_free_port(10000, reserved=[10000, 10001])
        assert port >= 10002


class TestCrypto:
    def test_password_length(self):
        pw = generate_password(32)
        assert len(pw) > 30

    def test_password_unique(self):
        pw1 = generate_password()
        pw2 = generate_password()
        assert pw1 != pw2


class TestInstanceContext:
    def test_paths_computed_correctly(self):
        ctx = InstanceContext(name="test", root=Path("/tmp/test"))
        assert ctx.config_path == Path("/tmp/test/config.json")
        assert ctx.secrets_path == Path("/tmp/test/secrets.env")
        assert ctx.certs_dir == Path("/tmp/test/data/certs")

    def test_ensure_dirs_creates_all(self, tmp_instance):
        assert tmp_instance.data_dir.exists()
        assert tmp_instance.certs_dir.exists()
        assert tmp_instance.nginx_conf_dir.exists()
