"""Host scheduler rendering and lifecycle tests."""

import plistlib
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import auto_update_scheduler as scheduler
from src.models.instance import InstanceContext


def _result(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


@pytest.fixture
def schedule_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    easy_opal_home = tmp_path / "easy opal home"
    instance = InstanceContext(name="Study Alpha!", root=tmp_path / "instance root")
    instance.root.mkdir()

    monkeypatch.setattr(scheduler, "_home_directory", lambda: home)
    monkeypatch.setattr(
        scheduler, "_check_python_entrypoint", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        scheduler.shutil,
        "which",
        lambda command: f"/usr/bin/{command}",
    )
    monkeypatch.setenv("EASY_OPAL_HOME", str(easy_opal_home))
    monkeypatch.setenv("PATH", "/opt/easy opal/bin:/usr/bin")
    for key in (
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "CONTAINER_CONNECTION",
        "CONTAINER_HOST",
        "CONTAINERS_CONF",
        "CONTAINERS_REGISTRIES_CONF",
        "CONTAINERS_STORAGE_CONF",
        "PODMAN_NO_PAUSE_PROCESS",
        "STORAGE_DRIVER",
        "STORAGE_OPTS",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    return instance, home, easy_opal_home


def _set_darwin(monkeypatch, uid=501):
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(scheduler.os, "getuid", lambda: uid)


def _set_linux_user(monkeypatch, config_home, uid=1000):
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduler.os, "getuid", lambda: uid)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))


def test_launchd_preflight_renders_safe_plist_without_writes(
    schedule_env, monkeypatch
):
    instance, home, easy_opal_home = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop<&team")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    plan = scheduler.preflight_auto_update_schedule(instance, "docker", 6)

    assert plan.backend == "launchd-user"
    assert re.fullmatch(
        r"org\.easyopal\.auto-update\.study-alpha-[0-9a-f]{12}",
        plan.identifier,
    )
    assert plan.command == (
        sys.executable,
        "-m",
        "src",
        "--runtime",
        "docker",
        "-i",
        instance.name,
        "auto-update",
        "--scheduled",
    )
    assert plan.environment == {
        "EASY_OPAL_HOME": str(easy_opal_home.absolute()),
        "HOME": str(home.absolute()),
        "PATH": "/opt/easy opal/bin:/usr/bin",
        "PYTHONPATH": "",
        "DOCKER_CONTEXT": "desktop<&team",
    }
    assert len(plan.files) == 1
    assert plan.files[0].path.parent == home / "Library" / "LaunchAgents"
    payload = plistlib.loads(plan.files[0].content)
    assert payload["Label"] == plan.identifier
    assert payload["ProgramArguments"] == list(plan.command)
    assert payload["EnvironmentVariables"] == plan.environment
    assert payload["StartInterval"] == 6 * 3600
    assert payload["Umask"] == 0o077
    assert payload["WorkingDirectory"] == str(instance.root.absolute())
    assert not plan.files[0].path.exists()
    assert calls == [["/usr/bin/launchctl", "print", "gui/501"]]


def test_scheduled_python_entrypoint_is_checked_from_job_working_directory(
    tmp_path, monkeypatch
):
    instance = InstanceContext("python-check", tmp_path)
    backend = SimpleNamespace(name="launchd-user")
    environment = {
        "HOME": str(Path.home()),
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": "",
    }
    calls = []
    expected = Path(scheduler.__file__).resolve().parents[1] / "__init__.py"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _result(command, stdout=f"{expected}\n")

    monkeypatch.setattr(scheduler, "_run", fake_run)

    scheduler._check_python_entrypoint(backend, instance, environment)

    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["env"] == environment


def test_systemd_user_preflight_escapes_values_without_writes(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config home"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    runtime = SimpleNamespace(
        name="podman",
        command="/custom/podman",
        env={"CONTAINER_HOST": 'ssh://user@host/run/%sock"quoted'},
    )
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    plan = scheduler.preflight_auto_update_schedule(instance, runtime, 12)

    assert plan.backend == "systemd-user"
    assert len(plan.files) == 2
    service = plan.files[0].content.decode()
    timer = plan.files[1].content.decode()
    assert plan.files[0].path.parent == config_home / "systemd" / "user"
    assert (
        'Environment="CONTAINER_HOST=ssh://user@host/run/%%sock\\"quoted"'
        in service
    )
    assert f'WorkingDirectory="{instance.root.absolute()}"' in service
    assert f'ExecStart="{sys.executable}" "-m" "src"' in service
    assert '"--runtime" "podman"' in service
    assert "OnActiveSec=12h" in timer
    assert "OnUnitActiveSec=12h" in timer
    assert f"Unit={plan.identifier}.service" in timer
    assert not plan.files[0].path.exists()
    assert not plan.files[1].path.exists()


def test_linux_root_uses_system_units(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 0)
    monkeypatch.setattr(scheduler, "_check_write_access", lambda _files: None)
    monkeypatch.setattr(
        scheduler, "_validate_system_service_inputs", lambda *args: None
    )
    monkeypatch.setattr(
        scheduler, "_validate_system_context_endpoint", lambda *args: None
    )
    monkeypatch.setattr(scheduler, "_check_manager", lambda backend: None)
    monkeypatch.setattr(
        scheduler, "_root_home_directory", lambda: Path("/root")
    )
    monkeypatch.setenv("HOME", "/tmp/attacker-home")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker.sock")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    plan = scheduler.preflight_auto_update_schedule(instance, "docker", 2)

    assert plan.backend == "systemd-system"
    assert all(
        artifact.path.parent == Path("/etc/systemd/system")
        for artifact in plan.files
    )
    assert plan.command[1:3] == ("-I", "-m")
    assert plan.environment["HOME"] == "/root"
    assert plan.environment["PYTHONNOUSERSITE"] == "1"
    assert 'WorkingDirectory="/"' in plan.files[0].content.decode()


def test_linux_root_rejects_user_controlled_python(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 0)
    monkeypatch.setattr(scheduler, "_check_write_access", lambda _files: None)
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker.sock")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(
        scheduler,
        "_assert_root_trusted_path",
        lambda path, description: (_ for _ in ()).throw(
            scheduler.AutoUpdateScheduleError(
                f"Refusing a root schedule because {description} is not root-controlled"
            )
        ),
    )

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="Refusing a root schedule"
    ):
        scheduler.preflight_auto_update_schedule(instance, "docker", 2)

    assert calls == []


def test_root_trust_rejects_instance_under_world_writable_parent(tmp_path):
    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="untrusted path component"
    ):
        scheduler._assert_root_trusted_path(tmp_path, "instance directory")


def test_root_trusted_tree_checks_bytecode_and_non_python_assets(
    tmp_path, monkeypatch
):
    package = tmp_path / "src"
    bytecode = package / "__pycache__" / "module.pyc"
    asset = package / "templates" / "nginx.conf"
    linked_asset = tmp_path / "linked-package" / "native.so"
    bytecode.parent.mkdir(parents=True)
    asset.parent.mkdir()
    linked_asset.parent.mkdir()
    bytecode.write_bytes(b"bytecode")
    asset.write_text("template")
    linked_asset.write_bytes(b"extension")
    (package / "linked").symlink_to(linked_asset.parent)
    checked = []

    def fake_trust(path, description):
        checked.append(Path(path))
        return Path(path)

    monkeypatch.setattr(scheduler, "_assert_root_trusted_path", fake_trust)

    scheduler._assert_root_trusted_tree(package, "easy-opal package")

    assert bytecode in checked
    assert asset in checked
    assert package / "linked" / "native.so" in checked


def test_root_python_entrypoint_probe_uses_isolated_mode(tmp_path, monkeypatch):
    instance = InstanceContext("root-python-check", tmp_path)
    backend = SimpleNamespace(name="systemd-system")
    environment = {
        "HOME": "/root",
        "PATH": str(Path(sys.executable).parent),
        "PYTHONPATH": "",
        "PYTHONNOUSERSITE": "1",
    }
    calls = []
    expected = Path(scheduler.__file__).resolve().parents[1] / "__init__.py"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _result(command, stdout=f"{expected}\n")

    monkeypatch.setattr(scheduler, "_run", fake_run)

    scheduler._check_python_entrypoint(backend, instance, environment)

    assert calls[0][0][1:3] == ["-I", "-c"]
    assert calls[0][1]["cwd"] == Path("/")
    assert calls[0][1]["env"] == environment


def test_root_environment_uses_passwd_home_not_inherited_home(monkeypatch):
    backend = SimpleNamespace(name="systemd-system")
    monkeypatch.setenv("HOME", "/tmp/attacker-home")
    monkeypatch.delenv("EASY_OPAL_HOME", raising=False)
    monkeypatch.setattr(
        scheduler, "_root_home_directory", lambda: Path("/trusted/root")
    )

    environment = scheduler._capture_base_environment(backend)

    assert environment["HOME"] == "/trusted/root"
    assert environment["EASY_OPAL_HOME"] == "/trusted/root/.easy-opal"
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_root_preflight_rejects_context_path_before_subprocess(
    schedule_env, monkeypatch, tmp_path
):
    instance, _, _ = schedule_env
    untrusted_config = tmp_path / "attacker-docker-config"
    untrusted_config.mkdir()
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    monkeypatch.setattr(scheduler.os, "getuid", lambda: 0)
    monkeypatch.setattr(scheduler, "_check_write_access", lambda _files: None)
    monkeypatch.setattr(scheduler, "_root_home_directory", lambda: tmp_path)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("DOCKER_CONFIG", str(untrusted_config))
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/docker.sock")
    calls = []

    def fake_trust(path, description):
        if description == "DOCKER_CONFIG":
            raise scheduler.AutoUpdateScheduleError("untrusted DOCKER_CONFIG")
        return Path(path).absolute()

    monkeypatch.setattr(scheduler, "_assert_root_trusted_path", fake_trust)
    monkeypatch.setattr(
        scheduler,
        "_assert_root_trusted_executable",
        lambda path, description: Path(path),
    )

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="untrusted DOCKER_CONFIG"
    ):
        scheduler.preflight_auto_update_schedule(instance, "docker", 2)

    assert calls == []


def test_root_schedule_rejects_unstructured_podman_storage_options():
    details = scheduler._RuntimeDetails("podman", "podman", {})

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="STORAGE_OPTS is not supported"
    ):
        scheduler._validate_root_context_paths(
            details,
            {
                "HOME": "/root",
                "STORAGE_OPTS": "overlay.imagestore=/tmp/untrusted",
            },
        )


@pytest.mark.parametrize(
    "endpoint", ["tcp://engine:2375", "ssh://host/run/podman.sock"]
)
def test_root_schedule_rejects_remote_container_endpoints(endpoint):
    with pytest.raises(
        scheduler.AutoUpdateScheduleError,
        match="only support local unix",
    ):
        scheduler._assert_root_trusted_socket(endpoint, "CONTAINER_HOST")


def test_root_podman_provider_is_validated(monkeypatch):
    details = scheduler._RuntimeDetails("podman", "podman", {})
    environment = {"PATH": "/trusted/bin", "HOME": "/root"}
    checked = []

    monkeypatch.setattr(
        scheduler.shutil,
        "which",
        lambda command, path=None: f"/trusted/bin/{command}",
    )
    monkeypatch.setattr(
        scheduler,
        "_assert_root_trusted_executable",
        lambda path, description: (
            checked.append((Path(path), description)) or Path(path)
        ),
    )

    scheduler._validate_root_runtime_and_provider(details, environment)

    assert checked == [
        (Path("/trusted/bin/podman"), "podman executable"),
        (Path("/trusted/bin/podman-compose"), "podman-compose executable"),
    ]


@pytest.mark.parametrize("operation", ["status", "remove"])
def test_root_status_and_remove_never_execute_untrusted_systemctl(
    tmp_path, monkeypatch, operation
):
    instance = InstanceContext("root-manager-check", tmp_path / "instance")
    instance.root.mkdir()
    manager = tmp_path / "systemctl"
    manager.write_text("#!/bin/sh\nexit 0\n")
    manager.chmod(0o755)
    service = tmp_path / "job.service"
    timer = tmp_path / "job.timer"
    service.write_text("service")
    timer.write_text("timer")
    backend = scheduler._Backend(
        "systemd-system",
        "easy-opal-test",
        str(manager),
        None,
        (service, timer),
    )
    calls = []

    monkeypatch.setattr(scheduler, "_backend", lambda instance, job: backend)

    def fake_run(command, **kwargs):
        calls.append(command)
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="systemctl executable"
    ):
        if operation == "status":
            scheduler.auto_update_schedule_status(instance)
        else:
            scheduler.remove_auto_update_schedule(instance)

    assert calls == []
    assert service.exists() and timer.exists()


def test_docker_context_is_snapshotted_when_environment_is_unset(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == ["docker", "context", "show"]:
            return _result(command, stdout="desktop-linux\n")
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    plan = scheduler.preflight_auto_update_schedule(instance, "docker", 1)

    assert plan.environment["DOCKER_CONTEXT"] == "desktop-linux"
    assert ["docker", "context", "show"] in calls


def test_podman_default_connection_is_snapshotted(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    runtime = SimpleNamespace(
        name="podman",
        command="/opt/podman/bin/podman",
        env={"PODMAN_COMPOSE_PROVIDER": "podman-compose"},
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("env")))
        if command[1:] == ["system", "connection", "list", "--format", "json"]:
            return _result(
                command,
                stdout=(
                    '[{"Name":"podman-machine-default","Default":true},'
                    '{"Name":"podman-machine-default-root","Default":false}]'
                ),
            )
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    plan = scheduler.preflight_auto_update_schedule(instance, runtime, 4)

    assert plan.environment["CONTAINER_CONNECTION"] == "podman-machine-default"
    probe = next(item for item in calls if item[0][0] == "/opt/podman/bin/podman")
    assert probe[1]["PODMAN_COMPOSE_PROVIDER"] == "podman-compose"
    assert "PODMAN_COMPOSE_PROVIDER" not in plan.environment


def test_podman_custom_storage_and_config_context_is_snapshotted(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    context_root = instance.root.parent / "podman-context"
    config_home = context_root / "config"
    data_home = context_root / "data"
    runtime_dir = context_root / "run"
    temporary_dir = context_root / "tmp"
    for directory in (config_home, data_home, runtime_dir, temporary_dir):
        directory.mkdir(parents=True)
    paths = {
        "CONTAINERS_CONF": context_root / "containers.conf",
        "CONTAINERS_REGISTRIES_CONF": context_root / "registries.conf",
        "CONTAINERS_STORAGE_CONF": context_root / "storage.conf",
    }
    for path in paths.values():
        path.write_text("# test\n")
    environment = {
        **{key: str(path) for key, path in paths.items()},
        "CONTAINER_HOST": "ssh://core@127.0.0.1/run/user/1000/podman.sock",
        "PODMAN_NO_PAUSE_PROCESS": "1",
        "STORAGE_DRIVER": "overlay",
        "STORAGE_OPTS": "overlay.mount_program=/usr/bin/fuse-overlayfs",
        "TMPDIR": str(temporary_dir),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_DATA_HOME": str(data_home),
        "XDG_RUNTIME_DIR": str(runtime_dir),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    plan = scheduler.preflight_auto_update_schedule(instance, "podman", 4)

    for key, value in environment.items():
        assert plan.environment[key] == value


def test_unit_identifier_hash_distinguishes_equal_names(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    other = InstanceContext(instance.name, instance.root.parent / "other")

    first = scheduler._backend(instance).identifier
    second = scheduler._backend(other).identifier

    assert first != second
    assert first.rsplit("-", 1)[0] == second.rsplit("-", 1)[0]


@pytest.mark.parametrize("interval", [0, -1, 1.5, True])
def test_preflight_rejects_invalid_interval(schedule_env, interval):
    instance, _, _ = schedule_env
    with pytest.raises(scheduler.AutoUpdateScheduleError, match="interval"):
        scheduler.preflight_auto_update_schedule(instance, "docker", interval)


def test_preflight_rejects_unsupported_platform(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    monkeypatch.setattr(scheduler.platform, "system", lambda: "Windows")

    with pytest.raises(scheduler.AutoUpdateScheduleError, match="not supported"):
        scheduler.preflight_auto_update_schedule(instance, "docker", 1)


def test_preflight_propagates_manager_failure(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command, 5, stderr="manager unavailable"),
    )

    with pytest.raises(
        scheduler.AutoUpdateScheduleError,
        match="exit code 5.*manager unavailable",
    ):
        scheduler.preflight_auto_update_schedule(instance, "docker", 1)


def test_preflight_propagates_context_probe_failure(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)

    def fake_run(command, **kwargs):
        if command == ["docker", "context", "show"]:
            return _result(command, 7, stderr="context failure")
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="exit code 7.*context failure"
    ):
        scheduler.preflight_auto_update_schedule(instance, "docker", 1)


def test_preflight_rejects_control_characters_in_environment(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setenv("PATH", "/usr/bin\nExecStart=/bin/false")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    with pytest.raises(scheduler.AutoUpdateScheduleError, match="control character"):
        scheduler.preflight_auto_update_schedule(instance, "docker", 1)


def test_preflight_rejects_relative_path_entries(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    monkeypatch.setenv("PATH", "/usr/bin:relative/bin")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    with pytest.raises(scheduler.AutoUpdateScheduleError, match="absolute directories"):
        scheduler.preflight_auto_update_schedule(instance, "docker", 1)


def test_launchd_install_status_and_remove_are_idempotent(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    state = {"loaded": False, "bootstrap": 0, "bootout": 0}
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["print", "gui/501"] and len(command) == 3:
            return _result(command)
        if command[1] == "print" and len(command) == 3:
            if state["loaded"]:
                return _result(command)
            return _result(command, 113, stderr="Could not find service")
        if command[1] == "bootstrap":
            state["loaded"] = True
            state["bootstrap"] += 1
        elif command[1] == "bootout":
            state["loaded"] = False
            state["bootout"] += 1
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    first = scheduler.install_auto_update_schedule(instance, "docker", 6)
    schedule_path = first.paths[0]
    assert first.installed and first.enabled and first.active
    assert schedule_path.exists()
    assert schedule_path.stat().st_mode & 0o777 == 0o600

    second = scheduler.install_auto_update_schedule(instance, "docker", 6)
    assert second == first
    assert state["bootstrap"] == 1

    status = scheduler.auto_update_schedule_status(instance)
    assert status.installed and status.enabled and status.active

    scheduler.remove_auto_update_schedule(instance)
    assert not schedule_path.exists()
    assert state["bootout"] == 1
    call_count = len(calls)
    scheduler.remove_auto_update_schedule(instance)
    assert state["bootout"] == 1
    assert calls[call_count:][0][1] == "print"


def test_systemd_install_status_and_remove_are_idempotent(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    monkeypatch.setenv("CONTAINER_CONNECTION", "podman-machine-default")
    state = {"active": False, "enabled": False, "enable": 0, "disable": 0}
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "show" in command:
            enabled = "enabled" if state["enabled"] else "disabled"
            active = "active" if state["active"] else "inactive"
            return _result(
                command,
                stdout=(
                    f"LoadState=loaded\nUnitFileState={enabled}\n"
                    f"ActiveState={active}\n"
                ),
            )
        if "enable" in command:
            state["active"] = True
            state["enabled"] = True
            state["enable"] += 1
        if "disable" in command:
            state["active"] = False
            state["enabled"] = False
            state["disable"] += 1
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    first = scheduler.install_auto_update_schedule(instance, "podman", 8)
    assert first.installed and first.enabled and first.active
    assert all(path.exists() for path in first.paths)

    second = scheduler.install_auto_update_schedule(instance, "podman", 8)
    assert second == first
    assert state["enable"] == 1

    scheduler.remove_auto_update_schedule(instance)
    assert not any(path.exists() for path in first.paths)
    assert state["disable"] == 1
    call_count = len(calls)
    scheduler.remove_auto_update_schedule(instance)
    assert state["disable"] == 1
    assert calls[call_count:][0][2] == "show"


def test_launchd_bootstrap_failure_is_not_silenced(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")

    def fake_run(command, **kwargs):
        if command[1:3] == ["print", "gui/501"] and len(command) == 3:
            return _result(command)
        if command[1] == "print":
            return _result(command, 113, stderr="Could not find service")
        if command[1] == "bootstrap":
            return _result(command, 9, stderr="invalid plist")
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="exit code 9.*invalid plist"
    ):
        scheduler.install_auto_update_schedule(instance, "docker", 6)

    assert not scheduler._backend(instance).files[0].exists()


def test_systemd_enable_failure_is_not_silenced(schedule_env, monkeypatch):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")

    def fake_run(command, **kwargs):
        if "show" in command:
            return _result(
                command,
                stdout=(
                    "LoadState=not-found\nUnitFileState=disabled\n"
                    "ActiveState=inactive\n"
                ),
            )
        if "enable" in command:
            return _result(command, 4, stderr="enable failed")
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="exit code 4.*enable failed"
    ):
        scheduler.install_auto_update_schedule(instance, "docker", 6)

    assert not any(path.exists() for path in scheduler._backend(instance).files)


def test_launchd_failed_replacement_restores_previous_file_and_loaded_state(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    state = {"loaded": False, "fail_new": False}

    def fake_run(command, **kwargs):
        if command == ["/usr/bin/launchctl", "print", "gui/501"]:
            return _result(command)
        if command[1] == "print":
            if state["loaded"]:
                return _result(command)
            return _result(command, 113, stderr="Could not find service")
        if command[1] == "bootout":
            state["loaded"] = False
        elif command[1] == "bootstrap":
            payload = plistlib.loads(Path(command[-1]).read_bytes())
            if state["fail_new"] and payload["StartInterval"] == 6 * 3600:
                return _result(command, 9, stderr="invalid replacement plist")
            state["loaded"] = True
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    installed = scheduler.install_auto_update_schedule(instance, "docker", 4)
    previous_content = installed.paths[0].read_bytes()
    state["fail_new"] = True

    with pytest.raises(
        scheduler.AutoUpdateScheduleError,
        match="exit code 9.*invalid replacement plist",
    ):
        scheduler.install_auto_update_schedule(instance, "docker", 6)

    assert installed.paths[0].read_bytes() == previous_content
    assert plistlib.loads(previous_content)["StartInterval"] == 4 * 3600
    assert state["loaded"] is True
    assert scheduler.auto_update_schedule_status(instance).active


def test_systemd_failed_enable_restores_previous_units_and_timer_state(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    state = {"active": False, "enabled": False, "fail_new": False}

    def timer_is_replacement():
        timer = scheduler._backend(instance).files[1]
        return timer.exists() and "OnActiveSec=6h" in timer.read_text()

    def fake_run(command, **kwargs):
        if "show" in command:
            enabled = "enabled" if state["enabled"] else "disabled"
            active = "active" if state["active"] else "inactive"
            return _result(
                command,
                stdout=(
                    f"LoadState=loaded\nUnitFileState={enabled}\n"
                    f"ActiveState={active}\n"
                ),
            )
        if "disable" in command:
            state["enabled"] = False
            state["active"] = False
        elif "enable" in command:
            if state["fail_new"] and timer_is_replacement():
                state["enabled"] = False
                state["active"] = False
                return _result(command, 4, stderr="replacement enable failed")
            state["enabled"] = True
            state["active"] = "--now" in command
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    installed = scheduler.install_auto_update_schedule(instance, "docker", 4)
    previous_contents = tuple(path.read_bytes() for path in installed.paths)
    state["fail_new"] = True

    with pytest.raises(
        scheduler.AutoUpdateScheduleError,
        match="exit code 4.*replacement enable failed",
    ):
        scheduler.install_auto_update_schedule(instance, "docker", 6)

    assert tuple(path.read_bytes() for path in installed.paths) == previous_contents
    assert state["enabled"] is True
    assert state["active"] is True


def test_systemd_failed_reload_restores_previous_units_and_timer_state(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    state = {"active": False, "enabled": False, "fail_reload": False}

    def fake_run(command, **kwargs):
        if "show" in command:
            enabled = "enabled" if state["enabled"] else "disabled"
            active = "active" if state["active"] else "inactive"
            return _result(
                command,
                stdout=(
                    f"LoadState=loaded\nUnitFileState={enabled}\n"
                    f"ActiveState={active}\n"
                ),
            )
        if "daemon-reload" in command and state["fail_reload"]:
            state["fail_reload"] = False
            return _result(command, 5, stderr="reload failed")
        if "disable" in command:
            state["enabled"] = False
            state["active"] = False
        elif "enable" in command:
            state["enabled"] = True
            state["active"] = "--now" in command
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    installed = scheduler.install_auto_update_schedule(instance, "docker", 4)
    previous_contents = tuple(path.read_bytes() for path in installed.paths)
    state["fail_reload"] = True

    with pytest.raises(
        scheduler.AutoUpdateScheduleError, match="exit code 5.*reload failed"
    ):
        scheduler.install_auto_update_schedule(instance, "docker", 6)

    assert tuple(path.read_bytes() for path in installed.paths) == previous_contents
    assert state["enabled"] is True
    assert state["active"] is True


def test_failed_systemd_removal_rolls_back_and_can_be_retried(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/docker.sock")
    state = {"active": False, "enabled": False, "fail_remove_reload": False}

    def fake_run(command, **kwargs):
        paths = scheduler._backend(instance).files
        if "show" in command:
            enabled = "enabled" if state["enabled"] else "disabled"
            active = "active" if state["active"] else "inactive"
            load = "loaded" if any(path.exists() for path in paths) else "not-found"
            return _result(
                command,
                stdout=(
                    f"LoadState={load}\nUnitFileState={enabled}\n"
                    f"ActiveState={active}\n"
                ),
            )
        if "disable" in command:
            state["enabled"] = False
            state["active"] = False
        elif "enable" in command:
            state["enabled"] = True
            state["active"] = "--now" in command
        if (
            "daemon-reload" in command
            and state["fail_remove_reload"]
            and not any(path.exists() for path in paths)
        ):
            state["fail_remove_reload"] = False
            return _result(command, 6, stderr="remove reload failed")
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    installed = scheduler.install_auto_update_schedule(instance, "docker", 4)
    previous_contents = tuple(path.read_bytes() for path in installed.paths)
    state["fail_remove_reload"] = True

    with pytest.raises(
        scheduler.AutoUpdateScheduleError,
        match="exit code 6.*remove reload failed",
    ):
        scheduler.remove_auto_update_schedule(instance)

    assert tuple(path.read_bytes() for path in installed.paths) == previous_contents
    assert state["enabled"] is True
    assert state["active"] is True

    scheduler.remove_auto_update_schedule(instance)
    scheduler.remove_auto_update_schedule(instance)
    assert not any(path.exists() for path in installed.paths)
    assert state["enabled"] is False
    assert state["active"] is False


def test_launchd_removal_unloads_orphaned_job_without_plist(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    state = {"loaded": True, "bootout": 0}

    def fake_run(command, **kwargs):
        if command[1] == "print":
            if state["loaded"]:
                return _result(command)
            return _result(command, 113, stderr="Could not find service")
        if command[1] == "bootout":
            state["loaded"] = False
            state["bootout"] += 1
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    scheduler.remove_auto_update_schedule(instance)
    scheduler.remove_auto_update_schedule(instance)

    assert state == {"loaded": False, "bootout": 1}


def test_systemd_removal_disables_orphaned_timer_without_unit_files(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    state = {"active": True, "enabled": True, "disable": 0, "reload": 0}

    def fake_run(command, **kwargs):
        if "show" in command:
            enabled = "enabled" if state["enabled"] else "disabled"
            active = "active" if state["active"] else "inactive"
            return _result(
                command,
                stdout=(
                    f"LoadState=loaded\nUnitFileState={enabled}\n"
                    f"ActiveState={active}\n"
                ),
            )
        if "disable" in command:
            state["enabled"] = False
            state["active"] = False
            state["disable"] += 1
        elif "daemon-reload" in command:
            state["reload"] += 1
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)

    scheduler.remove_auto_update_schedule(instance)
    scheduler.remove_auto_update_schedule(instance)

    assert state == {
        "active": False,
        "enabled": False,
        "disable": 1,
        "reload": 1,
    }


def test_disabled_job_without_files_ignores_unavailable_systemd_user_bus(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)

    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(
            command,
            1,
            stderr="Failed to connect to bus: No medium found",
        ),
    )

    scheduler.remove_auto_update_schedule(instance)
    scheduler.remove_backup_schedule(instance)
    scheduler.remove_profile_update_schedule(instance)


def test_atomic_write_refuses_schedule_symlink(schedule_env, tmp_path):
    instance, _, _ = schedule_env
    target = tmp_path / "target"
    target.write_text("do not replace")
    link = tmp_path / "schedule"
    link.symlink_to(target)

    with pytest.raises(scheduler.AutoUpdateScheduleError, match="symlink"):
        scheduler._atomic_write(scheduler.ScheduleFile(link, b"replacement"))

    assert target.read_text() == "do not replace"


def test_artifact_match_repairs_insecure_mode(tmp_path):
    path = tmp_path / "schedule"
    path.write_bytes(b"same content")
    path.chmod(0o644)
    artifact = scheduler.ScheduleFile(path, b"same content", 0o600)

    assert not scheduler._artifact_matches(artifact)
    scheduler._atomic_write(artifact)

    assert scheduler._artifact_matches(artifact)
    assert path.stat().st_mode & 0o777 == 0o600


def test_all_jobs_render_distinct_launchd_plists_and_exact_commands(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    update = scheduler.preflight_auto_update_schedule(
        instance, "docker", 3, cleanup=True
    )
    backup = scheduler.preflight_backup_schedule(instance, "docker", 4)
    profile = scheduler.preflight_profile_update_schedule(instance, "docker", 5)
    prefix = (
        sys.executable,
        "-m",
        "src",
        "--runtime",
        "docker",
        "-i",
        instance.name,
    )

    assert update.command == prefix + ("auto-update", "--scheduled", "--cleanup")
    assert backup.command == prefix + ("backup", "create", "--scheduled")
    assert profile.command == prefix + (
        "profile",
        "pull",
        "--no-apply",
        "--scheduled",
    )
    assert {update.interval_hours, backup.interval_hours, profile.interval_hours} == {
        3,
        4,
        5,
    }
    assert len({update.identifier, backup.identifier, profile.identifier}) == 3
    assert ".auto-update." in update.identifier
    assert ".backup." in backup.identifier
    assert ".profile-update." in profile.identifier
    assert len({plan.files[0].path for plan in (update, backup, profile)}) == 3
    for plan in (update, backup, profile):
        payload = plistlib.loads(plan.files[0].content)
        assert payload["Label"] == plan.identifier
        assert payload["ProgramArguments"] == list(plan.command)
        assert payload["StartInterval"] == plan.interval_hours * 3600
        assert not plan.files[0].path.exists()


@pytest.mark.parametrize("cleanup", [None, 0, 1, "yes"])
def test_auto_update_preflight_rejects_non_boolean_cleanup(
    schedule_env, cleanup
):
    instance, _, _ = schedule_env

    with pytest.raises(scheduler.AutoUpdateScheduleError, match="cleanup"):
        scheduler.preflight_auto_update_schedule(
            instance, "docker", 1, cleanup=cleanup
        )


def test_backup_and_profile_render_distinct_systemd_units(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    config_home = instance.root.parent / "config"
    config_home.mkdir()
    _set_linux_user(monkeypatch, config_home)
    monkeypatch.setenv("CONTAINER_CONNECTION", "podman-machine-default")
    monkeypatch.setattr(
        scheduler.subprocess,
        "run",
        lambda command, **kwargs: _result(command),
    )

    backup = scheduler.preflight_backup_schedule(instance, "podman", 7)
    profile = scheduler.preflight_profile_update_schedule(instance, "podman", 9)

    assert backup.identifier.startswith("easy-opal-backup-")
    assert profile.identifier.startswith("easy-opal-profile-update-")
    assert backup.identifier != profile.identifier
    assert set(artifact.path for artifact in backup.files).isdisjoint(
        artifact.path for artifact in profile.files
    )
    backup_service = backup.files[0].content.decode()
    profile_service = profile.files[0].content.decode()
    assert '"backup" "create" "--scheduled"' in backup_service
    assert '"profile" "pull" "--no-apply" "--scheduled"' in profile_service
    assert "OnUnitActiveSec=7h" in backup.files[1].content.decode()
    assert "OnUnitActiveSec=9h" in profile.files[1].content.decode()


@pytest.mark.parametrize(
    ("install_name", "status_name", "remove_name", "job_slug", "command_tail"),
    [
        (
            "install_backup_schedule",
            "backup_schedule_status",
            "remove_backup_schedule",
            "backup",
            ["backup", "create", "--scheduled"],
        ),
        (
            "install_profile_update_schedule",
            "profile_update_schedule_status",
            "remove_profile_update_schedule",
            "profile-update",
            ["profile", "pull", "--no-apply", "--scheduled"],
        ),
    ],
)
def test_additional_launchd_jobs_install_status_and_remove_idempotently(
    schedule_env,
    monkeypatch,
    install_name,
    status_name,
    remove_name,
    job_slug,
    command_tail,
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "desktop-linux")
    loaded = set()
    counts = {"bootstrap": 0, "bootout": 0}

    def fake_run(command, **kwargs):
        if command == ["/usr/bin/launchctl", "print", "gui/501"]:
            return _result(command)
        if command[1] == "print":
            if command[-1] in loaded:
                return _result(command)
            return _result(command, 113, stderr="Could not find service")
        if command[1] == "bootstrap":
            identifier = Path(command[-1]).stem
            loaded.add(f"gui/501/{identifier}")
            counts["bootstrap"] += 1
        elif command[1] == "bootout":
            loaded.discard(command[-1])
            counts["bootout"] += 1
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    install = getattr(scheduler, install_name)
    get_status = getattr(scheduler, status_name)
    remove = getattr(scheduler, remove_name)

    first = install(instance, "docker", 6)
    assert first.installed and first.enabled and first.active
    assert f".{job_slug}." in first.identifier
    payload = plistlib.loads(first.paths[0].read_bytes())
    assert payload["ProgramArguments"][-len(command_tail) :] == command_tail

    assert install(instance, "docker", 6) == first
    assert counts["bootstrap"] == 1
    assert get_status(instance) == first

    remove(instance)
    assert not first.paths[0].exists()
    assert counts["bootout"] == 1
    remove(instance)
    assert counts["bootout"] == 1


def test_removing_one_job_does_not_touch_other_job_files(
    schedule_env, monkeypatch
):
    instance, _, _ = schedule_env
    _set_darwin(monkeypatch)
    monkeypatch.setenv("DOCKER_CONTEXT", "default")
    loaded = set()

    def fake_run(command, **kwargs):
        if command == ["/usr/bin/launchctl", "print", "gui/501"]:
            return _result(command)
        if command[1] == "print":
            return (
                _result(command)
                if command[-1] in loaded
                else _result(command, 113, stderr="Could not find service")
            )
        if command[1] == "bootstrap":
            loaded.add(f"gui/501/{Path(command[-1]).stem}")
        elif command[1] == "bootout":
            loaded.discard(command[-1])
        return _result(command)

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    update = scheduler.install_auto_update_schedule(instance, "docker", 2)
    backup = scheduler.install_backup_schedule(instance, "docker", 2)
    profile = scheduler.install_profile_update_schedule(instance, "docker", 2)

    scheduler.remove_backup_schedule(instance)

    assert not backup.paths[0].exists()
    assert update.paths[0].exists()
    assert profile.paths[0].exists()
    assert scheduler.auto_update_schedule_status(instance).active
    assert scheduler.profile_update_schedule_status(instance).active
