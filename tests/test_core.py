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
from src.core import docker, secrets_manager
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

    def test_config_file_is_private(self, tmp_instance):
        save_config(OpalConfig(), tmp_instance)

        mode = os.stat(tmp_instance.config_path).st_mode & 0o777
        assert mode == 0o600

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

    def test_atomic_secret_publish_failure_preserves_previous_file(
        self, tmp_instance, monkeypatch
    ):
        save_secrets({"KEY": "old"}, tmp_instance)
        temporary_modes = []

        def fail_replace(source, _destination):
            temporary_modes.append(os.stat(source).st_mode & 0o777)
            raise OSError("simulated publish failure")

        monkeypatch.setattr(secrets_manager.os, "replace", fail_replace)

        with pytest.raises(OSError, match="publish failure"):
            save_secrets({"KEY": "new"}, tmp_instance)

        assert load_secrets(tmp_instance) == {"KEY": "old"}
        assert temporary_modes == [0o600]
        assert not list(tmp_instance.root.glob(".secrets.env.*"))

    def test_secret_values_cannot_inject_additional_environment_lines(
        self, tmp_instance
    ):
        with pytest.raises(ValueError, match="newline or NUL"):
            save_secrets({"KEY": "value\nINJECTED=yes"}, tmp_instance)

        assert not tmp_instance.secrets_path.exists()


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

    def test_acme_challenge_never_starts_dependencies_and_always_stops_nginx(
        self, tmp_instance, monkeypatch
    ):
        cfg = OpalConfig(
            stack_name="study",
            hosts=["opal.example.org"],
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
        )
        calls = []
        nginx_modes = []
        monkeypatch.setattr(docker, "_nginx_is_running", lambda *_args: False)
        monkeypatch.setattr(docker, "generate_compose", lambda *_args: None)
        monkeypatch.setattr(
            "src.core.nginx.generate_nginx_config",
            lambda _cfg, _ctx, acme_only=False: nginx_modes.append(acme_only),
        )

        def run_compose(args, _ctx, _project):
            calls.append(args)
            return args[0] != "up"

        monkeypatch.setattr(docker, "run_compose", run_compose)

        assert not docker.obtain_letsencrypt_certificate(cfg, tmp_instance)
        assert calls == [
            ["up", "-d", "--no-deps", "--force-recreate", "nginx"],
            ["stop", "nginx"],
        ]
        assert nginx_modes == [True]

    def test_acme_success_restores_full_nginx_configuration(
        self, tmp_instance, monkeypatch
    ):
        cfg = OpalConfig(
            stack_name="study",
            hosts=["opal.example.org"],
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
        )
        calls = []
        nginx_modes = []
        compose_generations = []
        monkeypatch.setattr(docker, "_nginx_is_running", lambda *_args: False)
        monkeypatch.setattr(
            docker,
            "generate_compose",
            lambda *_args: compose_generations.append(True),
        )
        monkeypatch.setattr(
            "src.core.nginx.generate_nginx_config",
            lambda _cfg, _ctx, acme_only=False: nginx_modes.append(acme_only),
        )
        monkeypatch.setattr(
            docker,
            "run_compose",
            lambda args, _ctx, _project: calls.append(args) or True,
        )

        assert docker.obtain_letsencrypt_certificate(cfg, tmp_instance)
        assert calls[0] == [
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "nginx",
        ]
        assert calls[-1] == ["stop", "nginx"]
        assert any("certbot" in args for args in calls)
        assert nginx_modes == [True, False]
        assert len(compose_generations) == 2

    def test_acme_stop_failure_is_not_reported_as_success(
        self, tmp_instance, monkeypatch
    ):
        cfg = OpalConfig(
            stack_name="study",
            hosts=["opal.example.org"],
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
        )
        calls = []
        nginx_modes = []
        compose_generations = []
        monkeypatch.setattr(docker, "_nginx_is_running", lambda *_args: False)
        monkeypatch.setattr(
            docker,
            "generate_compose",
            lambda *_args: compose_generations.append(True),
        )
        monkeypatch.setattr(
            "src.core.nginx.generate_nginx_config",
            lambda _cfg, _ctx, acme_only=False: nginx_modes.append(acme_only),
        )

        def run_compose(args, _ctx, _project):
            calls.append(args)
            return args != ["stop", "nginx"]

        monkeypatch.setattr(docker, "run_compose", run_compose)

        with pytest.raises(RuntimeError, match="could not be stopped"):
            docker.obtain_letsencrypt_certificate(cfg, tmp_instance)

        assert calls[-1] == ["stop", "nginx"]
        assert nginx_modes == [True]
        assert len(compose_generations) == 1

    def test_acme_restores_nginx_when_it_was_running(
        self, tmp_instance, monkeypatch
    ):
        cfg = OpalConfig(
            stack_name="study",
            hosts=["opal.example.org"],
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
        )
        calls = []
        monkeypatch.setattr(docker, "_nginx_is_running", lambda *_args: True)
        monkeypatch.setattr(docker, "generate_compose", lambda *_args: None)
        monkeypatch.setattr(
            "src.core.nginx.generate_nginx_config", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            docker,
            "run_compose",
            lambda args, _ctx, _project: calls.append(args) or True,
        )

        result = docker.obtain_letsencrypt_certificate(cfg, tmp_instance)

        assert result.obtained
        assert result.nginx_was_running
        assert calls[0] == [
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "nginx",
        ]
        assert calls[-1] == calls[0]
        assert ["stop", "nginx"] in calls

    def test_acme_post_challenge_error_keeps_original_running_state(
        self, tmp_instance, monkeypatch
    ):
        cfg = OpalConfig(
            stack_name="study",
            hosts=["opal.example.org"],
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
        )
        monkeypatch.setattr(docker, "_nginx_is_running", lambda *_args: True)
        monkeypatch.setattr(docker, "generate_compose", lambda *_args: None)

        def generate_nginx(_cfg, _ctx, acme_only=False):
            if not acme_only:
                raise OSError("full NGINX generation failed")

        monkeypatch.setattr(
            "src.core.nginx.generate_nginx_config", generate_nginx
        )
        monkeypatch.setattr(docker, "run_compose", lambda *_args: True)

        with pytest.raises(docker.CertificateAcquisitionError) as exc_info:
            docker.obtain_letsencrypt_certificate(cfg, tmp_instance)

        assert exc_info.value.nginx_was_running is True


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
        assert (tmp_instance.letsencrypt_dir / "www").exists()
        assert (tmp_instance.letsencrypt_dir / "conf").exists()
