"""Security checks for support bundles and generated Compose files."""

import stat
import subprocess
import zipfile

import pytest
import yaml
from click.testing import CliRunner

from src.commands import support
from src.core import docker
from src.core.config_manager import save_config
from src.core.secrets_manager import save_secrets
from src.models.config import OpalConfig


class FakeRuntime:
    name = "podman"
    compose_command = ("podman", "compose")
    env = {}

    def __init__(self, secret: str):
        self.secret = secret

    def compose(self, *_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, f"status: {self.secret}\n", "")

    def run(self, args, **_kwargs):
        if args == ["--version"]:
            return subprocess.CompletedProcess([], 0, "podman version 5\n", "")
        return subprocess.CompletedProcess([], 0, f"log contains {self.secret}\n", "")


def test_support_bundle_redacts_compose_runtime_output_and_is_private(
    tmp_instance, monkeypatch
):
    secret = "support-secret-sentinel"
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    save_secrets({"OPAL_ADMIN_PASSWORD": secret}, tmp_instance)
    tmp_instance.compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "opal": {
                        "environment": {
                            "OPAL_ADMINISTRATOR_PASSWORD": secret,
                            "PUBLIC_SETTING": "diagnostic-value",
                        },
                        "env_file": "/private/location/secrets.env",
                        "command": ["--credential", f"prefix-{secret}-suffix"],
                    },
                    "keycloak": {
                        "image": "quay.io/keycloak/keycloak:25.0.6",
                        "environment": {"KEYCLOAK_ADMIN_PASSWORD": secret},
                    },
                }
            }
        )
    )
    monkeypatch.setattr(support, "get_runtime", lambda _instance: FakeRuntime(secret))

    destination = tmp_instance.root / "bundle.zip"
    result = CliRunner().invoke(
        support.support_bundle,
        ["--output", str(destination)],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with zipfile.ZipFile(destination) as archive:
        members = archive.namelist()
        contents = b"".join(archive.read(name) for name in members)
        compose_name = next(name for name in members if name.endswith("/compose.yml"))
        compose = yaml.safe_load(archive.read(compose_name))

    assert secret.encode() not in contents
    assert b"/private/location/secrets.env" not in contents
    service = compose["services"]["opal"]
    assert set(service["environment"].values()) == {"***REDACTED***"}
    assert service["env_file"] == "***REDACTED***"
    assert service["command"] == ["--credential", "prefix-***REDACTED***-suffix"]
    assert compose["services"]["keycloak"]["image"].startswith("quay.io/keycloak/")
    assert compose["services"]["keycloak"]["environment"] == {
        "KEYCLOAK_ADMIN_PASSWORD": "***REDACTED***"
    }
    assert "Known secret values are redacted" in result.output
    assert "review the bundle before sharing" in result.output


def test_support_bundle_publish_is_atomic(tmp_path):
    destination = tmp_path / "support.zip"
    destination.write_bytes(b"previous bundle")

    with pytest.raises(RuntimeError, match="collection failed"):
        with support._private_zip_file(destination):
            raise RuntimeError("collection failed")

    assert destination.read_bytes() == b"previous bundle"
    assert list(tmp_path.glob(".support.zip.*")) == []


def test_generate_compose_is_atomic_and_private(tmp_instance, monkeypatch):
    tmp_instance.compose_path.write_text("old compose")
    tmp_instance.compose_path.chmod(0o644)

    class Runtime:
        name = "podman"

    class Registry:
        def __init__(self, *_args, **_kwargs):
            pass

        def assemble_compose(self):
            return {"services": {"opal": {"image": "example/opal:latest"}}}

    monkeypatch.setattr(docker, "get_runtime", lambda _instance: Runtime())
    monkeypatch.setattr(docker, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(docker, "ensure_secrets", lambda *_args: {})
    monkeypatch.setattr(docker, "ServiceRegistry", Registry)

    docker.generate_compose(OpalConfig(), tmp_instance)

    assert "example/opal:latest" in tmp_instance.compose_path.read_text()
    assert stat.S_IMODE(tmp_instance.compose_path.stat().st_mode) == 0o600


def test_compose_atomic_write_preserves_previous_file_on_publish_failure(
    tmp_path, monkeypatch
):
    destination = tmp_path / "docker-compose.yml"
    destination.write_text("previous compose")
    monkeypatch.setattr(
        docker.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(OSError, match="publish failed"):
        docker._atomic_write_private_text(destination, "replacement compose")

    assert destination.read_text() == "previous compose"
    assert list(tmp_path.glob(".docker-compose.yml.*")) == []
