"""Container runtime selection, isolation, and instance binding."""

import json
import subprocess

import pytest

from src.core import container_runtime as cr
from src.core import docker as docker_core
from src.core import instance_manager as im
from src.core.config_manager import save_config
from src.models.config import OpalConfig
from src.models.instance import InstanceContext


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_OPAL_HOME", str(tmp_path))
    monkeypatch.delenv("EASY_OPAL_RUNTIME", raising=False)
    monkeypatch.setattr(cr, "_requested_runtime", None)
    monkeypatch.setattr(
        cr.shutil,
        "which",
        lambda command: "/usr/bin/podman-compose"
        if command == "podman-compose"
        else None,
    )


def _result(command, returncode=0, stdout=None, stderr=""):
    if stdout is None:
        if command == ["/usr/bin/podman-compose", "version"]:
            stdout = "podman-compose version 1.6.0\n"
        elif command == ["podman", "--version"]:
            stdout = "podman version 4.6.0\n"
        elif command == [
            "podman",
            "info",
            "--format",
            "{{.Version.Version}}",
        ]:
            stdout = "4.6.0\n"
        elif command in (
            ["docker", "compose", "up", "--help"],
            ["podman", "compose", "up", "--help"],
        ):
            stdout = "usage: compose up [--wait] [--wait-timeout SECONDS]\n"
        else:
            stdout = ""
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_auto_checks_complete_pair_then_falls_back_to_podman(monkeypatch):
    ctx = im.create_instance("study")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == ["docker", "compose", "version"]:
            return _result(command, returncode=1, stderr="compose missing")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    runtime = cr.get_runtime(ctx)

    assert runtime.name == "podman"
    assert ["docker", "compose", "version"] in calls
    assert ["podman", "compose", "version"] in calls
    assert im.get_instance_runtime(ctx) == "podman"


def test_auto_prefers_docker_and_persists_binding(monkeypatch):
    ctx = im.create_instance("study")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    assert cr.get_runtime(ctx).name == "docker"
    assert im.get_instance_runtime("study") == "docker"
    assert all(command[0] == "docker" for command in calls)


def test_auto_does_not_misclassify_podman_docker_shim(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == [
            "docker",
            "version",
            "--format",
            "{{.Server.Platform.Name}}",
        ]:
            return _result(command, stdout="Podman Engine\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    runtime = cr.get_runtime()

    assert runtime.name == "podman"
    assert ["podman", "compose", "version"] in calls


def test_explicit_podman_never_invokes_docker(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    assert cr.get_runtime().name == "podman"
    assert calls
    assert all(command[0] not in {"docker", "docker-compose"} for command in calls)


def test_unavailable_explicit_runtime_does_not_try_the_other(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        raise FileNotFoundError

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match="podman"):
        cr.get_runtime()
    assert calls == [["/usr/bin/podman-compose", "version"]]
    assert all(command[0] != "docker" for command in calls)


def test_podman_requires_independent_compose_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(cr.shutil, "which", lambda _command: None)
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or _result(command),
    )
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match="podman-compose"):
        cr.get_runtime()

    assert calls == []


def test_podman_rejects_unsupported_compose_provider_version(monkeypatch):
    def fake_run(command, **kwargs):
        if command == ["/usr/bin/podman-compose", "version"]:
            return _result(command, stdout="podman-compose version 1.5.0\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match=r">=1\.6\.0"):
        cr.get_runtime()


def test_podman_rejects_unknown_compose_provider_version(monkeypatch):
    def fake_run(command, **kwargs):
        if command == ["/usr/bin/podman-compose", "version"]:
            return _result(command, stdout="unknown provider build\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match="could not be determined"):
        cr.get_runtime()


def test_podman_rejects_unsupported_engine_version(monkeypatch):
    def fake_run(command, **kwargs):
        if command == ["podman", "--version"]:
            return _result(command, stdout="podman version 4.5.1\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match=r"Podman >=4\.6\.0"):
        cr.get_runtime()


def test_podman_rejects_unsupported_host_version(monkeypatch):
    def fake_run(command, **kwargs):
        if command == [
            "podman",
            "info",
            "--format",
            "{{.Version.Version}}",
        ]:
            return _result(command, stdout="4.5.2\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match=r"host 4\.5\.2"):
        cr.get_runtime()


def test_podman_host_version_ignores_stderr_warnings(monkeypatch):
    def fake_run(command, **kwargs):
        if command == [
            "podman",
            "info",
            "--format",
            "{{.Version.Version}}",
        ]:
            return _result(
                command,
                stdout="4.9.3\n",
                stderr="warning: systemd user session unavailable\n",
            )
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    assert cr.get_runtime().name == "podman"


def test_runtime_rejects_compose_without_wait_support(monkeypatch):
    def fake_run(command, **kwargs):
        if command == ["podman", "compose", "up", "--help"]:
            return _result(command, stdout="usage: compose up [-d]\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match="up --wait"):
        cr.get_runtime()


def test_runtime_rejects_compose_without_wait_timeout_support(monkeypatch):
    def fake_run(command, **kwargs):
        if command == ["podman", "compose", "up", "--help"]:
            return _result(command, stdout="usage: compose up [--wait]\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    with pytest.raises(cr.RuntimeSelectionError, match="up --wait-timeout"):
        cr.get_runtime()


def test_environment_is_programmatic_fallback(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    monkeypatch.setenv("EASY_OPAL_RUNTIME", "podman")

    assert cr.get_runtime().name == "podman"
    assert all(command[0] not in {"docker", "docker-compose"} for command in calls)


def test_invocation_choice_overrides_environment(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    monkeypatch.setenv("EASY_OPAL_RUNTIME", "podman")
    cr.set_requested_runtime("docker")

    assert cr.get_runtime().name == "docker"
    assert all(command[0] == "docker" for command in calls)


def test_binding_is_used_in_auto_even_when_docker_is_available(monkeypatch):
    ctx = im.create_instance("study")
    im.set_instance_runtime(ctx, "podman")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    assert cr.get_runtime(ctx).name == "podman"
    assert all(command[0] not in {"docker", "docker-compose"} for command in calls)


def test_legacy_instance_adopts_engine_that_owns_its_resources(monkeypatch):
    ctx = im.create_instance("study")
    save_config(OpalConfig(stack_name="study"), ctx)

    def fake_run(command, **kwargs):
        if (
            command[0] == "podman"
            and "label=io.podman.compose.project=study" in command
        ):
            return _result(command, stdout="podman-container\n")
        return _result(command)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    runtime = cr.get_runtime(ctx)

    assert runtime.name == "podman"
    assert im.get_instance_runtime(ctx) == "podman"


def test_legacy_instance_without_detectable_owner_requires_explicit_choice(
    monkeypatch,
):
    ctx = im.create_instance("study")
    save_config(OpalConfig(stack_name="study"), ctx)
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    with pytest.raises(cr.RuntimeSelectionError, match="Select its runtime once"):
        cr.get_runtime(ctx)

    assert im.get_instance_runtime(ctx) is None


def test_explicit_choice_cannot_contradict_binding(monkeypatch):
    ctx = im.create_instance("study")
    im.set_instance_runtime(ctx, "podman")
    calls = []
    monkeypatch.setattr(cr.subprocess, "run", lambda command, **kwargs: calls.append(command))
    cr.set_requested_runtime("docker")

    with pytest.raises(cr.RuntimeSelectionError, match="bound to podman"):
        cr.get_runtime(ctx)
    assert calls == []


def test_invalid_registry_binding_is_not_executed(monkeypatch):
    ctx = im.create_instance("study")
    registry = im._load_registry()
    registry["instances"]["study"]["runtime"] = "containerd"
    im._save_registry(registry)
    calls = []
    monkeypatch.setattr(cr.subprocess, "run", lambda command, **kwargs: calls.append(command))

    with pytest.raises(cr.RuntimeSelectionError, match="invalid runtime binding"):
        cr.get_runtime(ctx)
    assert calls == []


def test_podman_compose_provider_is_pinned_and_inherited(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"].copy()))
        return _result(command)

    monkeypatch.setattr(cr.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.set_requested_runtime("podman")

    runtime = cr.get_runtime()
    ctx = InstanceContext(name="study", root=tmp_path / "study")
    runtime.compose(["ps"], ctx, project_name="study", check=False)

    assert runtime.compose_command == ("podman", "compose")
    assert runtime.env == {"PODMAN_COMPOSE_PROVIDER": "podman-compose"}
    assert all(
        env["PODMAN_COMPOSE_PROVIDER"] == "podman-compose"
        for command, env in calls
        if command[0] == "podman"
    )
    assert calls[-1][0] == [
        "podman",
        "compose",
        "--project-name",
        "study",
        "-f",
        str(ctx.compose_path),
        "ps",
    ]


def test_compose_can_use_an_explicit_snapshot_file(monkeypatch, tmp_path):
    calls = []
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    ctx = InstanceContext(name="study", root=tmp_path / "study")
    snapshot = ctx.root / ".update-snapshot.yml"
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or _result(command),
    )

    runtime.compose(
        ["up", "-d"],
        ctx,
        project_name="study",
        compose_file=snapshot,
        check=False,
    )

    assert calls == [[
        "podman",
        "compose",
        "--project-name",
        "study",
        "-f",
        str(snapshot),
        "up",
        "-d",
    ]]


def test_pull_qualifies_short_image_names(monkeypatch):
    calls = []
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or _result(command),
    )

    runtime.pull("datashield/rock-base:latest")

    assert calls == [["podman", "pull", "docker.io/datashield/rock-base:latest"]]


def test_project_volumes_accept_docker_and_podman_labels(monkeypatch):
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))

    def fake_run(command, **kwargs):
        output = (
            "study_study-db-data\n"
            if "label=io.podman.compose.project=study" in command
            else ""
        )
        return _result(command, stdout=output)

    monkeypatch.setattr(cr.subprocess, "run", fake_run)

    assert cr.list_project_volumes(runtime, "study") == [
        "study_study-db-data"
    ]


def test_project_volume_query_failure_is_not_reported_as_empty(monkeypatch):
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: _result(
            command, returncode=1, stderr="permission denied"
        ),
    )

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        cr.list_project_volumes(runtime, "study")

    assert exc_info.value.stderr == "permission denied"


def test_check_runtime_returns_false_instead_of_exiting(monkeypatch):
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: _result(command, returncode=1),
    )
    cr.set_requested_runtime("docker")

    assert cr.check_runtime() is False


def test_rootless_podman_rejects_privileged_ports():
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))

    with pytest.raises(cr.RuntimeSelectionError, match=r"host port\(s\) 443"):
        cr.validate_runtime_config(
            runtime, OpalConfig(), port_threshold=1024
        )

    allowed = OpalConfig(opal_external_port=8443)
    cr.validate_runtime_config(runtime, allowed, port_threshold=1024)


def test_rootless_podman_reports_letsencrypt_port_80():
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    config = OpalConfig(
        opal_external_port=8443,
        ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
    )

    with pytest.raises(cr.RuntimeSelectionError, match=r"host port\(s\) 80"):
        cr.validate_runtime_config(runtime, config, port_threshold=1024)


def test_rootless_podman_checks_internal_database_and_mailpit_ports():
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    config = OpalConfig(
        opal_external_port=8443,
        databases=[
            {
                "type": "postgres",
                "name": "study",
                "port": 543,
            },
            {
                "type": "mysql",
                "name": "external",
                "port": 330,
                "external": True,
            },
        ],
        agate={"enabled": True, "mail_mode": "mailpit", "mailpit_port": 25},
    )

    with pytest.raises(
        cr.RuntimeSelectionError, match=r"host port\(s\) 25, 543"
    ):
        cr.validate_runtime_config(runtime, config, port_threshold=1024)


def test_rootless_podman_checks_enabled_armadillo_keycloak_port():
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    config = OpalConfig(
        flavor="armadillo",
        opal_external_port=8443,
        keycloak={"enabled": True, "port": 443},
    )

    with pytest.raises(cr.RuntimeSelectionError, match=r"host port\(s\) 443"):
        cr.validate_runtime_config(runtime, config, port_threshold=1024)


def test_remote_podman_does_not_use_local_rootless_port_threshold(monkeypatch):
    runtime = cr.ContainerRuntime(
        "podman",
        "podman",
        ("podman", "compose"),
        env={"CONTAINER_HOST": "ssh://podman.example/run/user/1000/podman.sock"},
    )
    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("remote engine should not be probed for a local threshold")
        ),
    )

    assert cr.rootless_port_threshold(runtime) is None


def test_default_remote_podman_does_not_use_client_port_threshold(monkeypatch):
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))
    path_reads = []

    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: _result(
            command,
            stdout=json.dumps(
                {
                    "host": {
                        "serviceIsRemote": True,
                        "security": {"rootless": True},
                    }
                }
            ),
        ),
    )
    monkeypatch.setattr(
        cr,
        "Path",
        lambda _path: type(
            "ProcPath",
            (),
            {"read_text": lambda self: path_reads.append(True) or "1024"},
        )(),
    )

    assert cr.rootless_port_threshold(runtime) is None
    assert path_reads == []


def test_local_rootless_podman_uses_local_port_threshold(monkeypatch):
    runtime = cr.ContainerRuntime("podman", "podman", ("podman", "compose"))

    monkeypatch.setattr(
        cr.subprocess,
        "run",
        lambda command, **kwargs: _result(
            command,
            stdout=json.dumps(
                {
                    "host": {
                        "serviceIsRemote": False,
                        "security": {"rootless": True},
                    }
                }
            ),
        ),
    )
    monkeypatch.setattr(
        cr,
        "Path",
        lambda _path: type(
            "ProcPath",
            (),
            {"read_text": lambda self: "1024\n"},
        )(),
    )

    assert cr.rootless_port_threshold(runtime) == 1024


def test_podman_up_uses_supported_compose_wait(tmp_path, monkeypatch):
    ctx = InstanceContext(name="study", root=tmp_path / "study")
    ctx.ensure_dirs()
    ctx.compose_path.write_text(
        "services:\n"
        "  opal:\n"
        "    container_name: study-opal\n"
        "    healthcheck:\n"
        "      test: [CMD, true]\n"
    )

    class Runtime:
        name = "podman"
        compose_command = ("podman", "compose")

        def __init__(self):
            self.compose_calls = []

        def compose(self, args, instance, project_name=None, **kwargs):
            self.compose_calls.append(args)
            return _result(args)

    runtime = Runtime()
    monkeypatch.setattr(docker_core, "get_runtime", lambda instance=None: runtime)
    monkeypatch.setattr(docker_core, "generate_compose", lambda *args: None)
    monkeypatch.setattr(
        "src.core.nginx.generate_nginx_config", lambda *args, **kwargs: None
    )

    assert docker_core.compose_up(ctx, OpalConfig(stack_name="study"))
    assert runtime.compose_calls == [
        ["up", "-d", "--remove-orphans", "--wait"]
    ]


def test_invalid_runtime_values_are_rejected(monkeypatch):
    with pytest.raises(ValueError, match="Invalid container runtime"):
        cr.set_requested_runtime("containerd")

    monkeypatch.setenv("EASY_OPAL_RUNTIME", "containerd")
    with pytest.raises(cr.RuntimeSelectionError, match="EASY_OPAL_RUNTIME"):
        cr.get_runtime()
