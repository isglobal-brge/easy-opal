"""Verified, runtime-neutral container image updates."""

import json
import stat
import subprocess

import pytest
import yaml
from click.testing import CliRunner

from src.core.auto_update import (
    AutoUpdateError,
    ScheduledUpdateDisabled,
    update_instance,
)
from src.core.config_manager import save_config
from src.core.instance_manager import InstanceLock
from src.models.config import OpalConfig


class FakeRuntime:
    """Stateful engine double that deliberately exposes no CLI binary."""

    name = "podman"

    def __init__(
        self,
        services: dict[str, tuple[str, str]],
        *,
        pulled: dict[str, str] | None = None,
        pull_failures: set[str] | None = None,
        pull_timeouts: set[str] | None = None,
        compose_codes: list[int] | None = None,
        compose_healths: list[str] | None = None,
        cleanup_failures: set[str] | None = None,
        health_style: str = "podman",
    ):
        self.containers = {
            service: {
                "reference": reference,
                "image_id": image_id,
                "running": True,
                "health": "healthy",
                "one_off": False,
            }
            for service, (reference, image_id) in services.items()
        }
        self.local_images = {
            reference: image_id for reference, image_id in services.values()
        }
        self.pulled = pulled or {}
        self.pull_failures = pull_failures or set()
        self.pull_timeouts = pull_timeouts or set()
        self.compose_codes = list(compose_codes or [])
        self.compose_healths = list(compose_healths or [])
        self.cleanup_failures = cleanup_failures or set()
        self.health_style = health_style
        self.run_calls: list[list[str]] = []
        self.pull_calls: list[str] = []
        self.compose_calls: list[list[str]] = []
        self.compose_snapshot_paths = []
        self.compose_snapshots: list[bytes | None] = []
        self.compose_snapshot_modes: list[int | None] = []
        self.removed_ids: list[str] = []

    @staticmethod
    def _result(args, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def run(self, args, **kwargs):
        self.run_calls.append(args)

        if args[:2] == ["ps", "--filter"]:
            output = "\n".join(
                f"container-{service}"
                for service, state in self.containers.items()
                if state["running"]
            )
            return self._result(args, stdout=f"{output}\n" if output else "")

        if args[0] == "inspect":
            records = []
            for container_id in args[1:]:
                service = container_id.removeprefix("container-")
                state = self.containers[service]
                labels = {
                    "io.podman.compose.project": "study",
                    "io.podman.compose.service": service,
                }
                if state["one_off"]:
                    labels["com.docker.compose.oneoff"] = "True"
                inspected_state = {
                    "Running": state["running"],
                    "Status": "running" if state["running"] else "exited",
                }
                health_key = "Health" if self.health_style == "docker" else "Healthcheck"
                inspected_state[health_key] = {"Status": state["health"]}
                records.append(
                    {
                        "Id": container_id,
                        "Image": state["image_id"],
                        "ImageName": state["reference"],
                        "Config": {"Image": state["reference"], "Labels": labels},
                        "State": inspected_state,
                    }
                )
            return self._result(args, stdout=json.dumps(records))

        if args[:2] == ["image", "inspect"]:
            reference = args[2]
            image_id = self.local_images.get(reference)
            if image_id is None:
                return self._result(args, 1, stderr="image not found")
            return self._result(args, stdout=json.dumps([{"Id": image_id}]))

        if args[0] == "tag":
            image_id, reference = args[1:]
            self.local_images[reference] = image_id
            return self._result(args)

        if args[:2] == ["image", "rm"]:
            image_id = args[2]
            if image_id in self.cleanup_failures:
                return self._result(args, 1, stderr="image is in use")
            self.removed_ids.append(image_id)
            return self._result(args)

        raise AssertionError(f"Unexpected runtime call: {args}")

    def pull(self, image, **kwargs):
        self.pull_calls.append(image)
        if image in self.pull_timeouts:
            raise subprocess.TimeoutExpired(["pull", image], kwargs.get("timeout", 1))
        if image in self.pull_failures:
            return self._result(["pull", image], 1, stderr="registry unavailable")
        self.local_images[image] = self.pulled.get(image, self.local_images[image])
        return self._result(["pull", image])

    def compose(self, args, instance, project_name=None, **kwargs):
        self.compose_calls.append(args)
        compose_file = kwargs.get("compose_file")
        self.compose_snapshot_paths.append(compose_file)
        self.compose_snapshots.append(
            compose_file.read_bytes() if compose_file is not None else None
        )
        self.compose_snapshot_modes.append(
            stat.S_IMODE(compose_file.stat().st_mode)
            if compose_file is not None
            else None
        )
        targets = [part for part in args if part in self.containers]
        code = self.compose_codes.pop(0) if self.compose_codes else 0
        health = self.compose_healths.pop(0) if self.compose_healths else "healthy"
        for service in targets:
            state = self.containers[service]
            state["image_id"] = self.local_images[state["reference"]]
            state["running"] = True
            state["health"] = health
        stderr = "health check failed" if code else ""
        return self._result(args, code, stderr=stderr)


def _prepare(tmp_instance, services):
    tmp_instance.compose_path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    service: {"image": reference}
                    for service, (reference, _) in services.items()
                }
            }
        )
    )


def test_no_change_pulls_without_restarting(tmp_instance):
    image = "docker.io/library/alpine:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services)

    result = update_instance(runtime, tmp_instance, "study")

    assert result.changed_images == ()
    assert runtime.pull_calls == [image]
    assert runtime.compose_calls == []


def test_partial_pull_failure_restores_tags_without_restarting(tmp_instance):
    first = "docker.io/example/app:latest"
    second = "quay.io/example/db:latest"
    services = {"app": (first, "first-old"), "db": (second, "second-old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={first: "first-new"},
        pull_failures={second},
    )

    with pytest.raises(AutoUpdateError, match="pull failed.*tags restored"):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.local_images == {first: "first-old", second: "second-old"}
    assert runtime.compose_calls == []


@pytest.mark.parametrize("health_style", ["docker", "podman"])
def test_healthy_update_verifies_ids_and_cleans_previous_image(
    tmp_instance, health_style
):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={image: "new"},
        health_style=health_style,
    )
    compose_snapshot = tmp_instance.compose_path.read_bytes()

    result = update_instance(runtime, tmp_instance, "study", cleanup=True)

    assert result.changed_images == (image,)
    assert runtime.compose_calls == [[
        "up", "-d", "--force-recreate", "--no-deps", "--pull", "never",
        "--wait", "--wait-timeout", "600", "app",
    ]]
    assert runtime.removed_ids == ["old"]
    assert runtime.compose_snapshots == [compose_snapshot]
    assert runtime.compose_snapshot_modes == [0o600]
    assert not runtime.compose_snapshot_paths[0].exists()


def test_stopped_service_is_not_pulled_or_started(tmp_instance):
    app = "docker.io/example/app:latest"
    db = "docker.io/example/db:latest"
    services = {"app": (app, "app-old"), "db": (db, "db-old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services, pulled={app: "app-new", db: "db-new"})
    runtime.containers["db"]["running"] = False

    result = update_instance(runtime, tmp_instance, "study")

    assert result.changed_images == (app,)
    assert runtime.pull_calls == [app]
    assert runtime.containers["db"]["running"] is False
    assert runtime.compose_calls[0][-1] == "app"
    assert "db" not in runtime.compose_calls[0]


def test_compose_reference_drift_aborts_before_pull(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, {"app": ("docker.io/example/app:next", "unused")})
    runtime = FakeRuntime(services, pulled={image: "new"})

    with pytest.raises(AutoUpdateError, match="current Compose file specifies"):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.pull_calls == []
    assert runtime.compose_calls == []


def test_any_compose_drift_during_pull_aborts_before_apply(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services, pulled={image: "new"})
    original_pull = runtime.pull

    def pull_and_change_environment(reference, **kwargs):
        result = original_pull(reference, **kwargs)
        document = yaml.safe_load(tmp_instance.compose_path.read_text())
        document["services"]["app"]["environment"] = {"TOKEN": "changed"}
        tmp_instance.compose_path.write_text(yaml.safe_dump(document))
        return result

    runtime.pull = pull_and_change_environment

    with pytest.raises(AutoUpdateError, match="Compose file changed.*tags restored"):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.local_images[image] == "old"
    assert runtime.compose_calls == []


def test_compose_drift_after_failed_apply_prevents_rollback(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={image: "new"},
        compose_codes=[1],
    )
    original_snapshot = tmp_instance.compose_path.read_bytes()
    original_compose = runtime.compose

    def compose_and_change_environment(*args, **kwargs):
        result = original_compose(*args, **kwargs)
        document = yaml.safe_load(tmp_instance.compose_path.read_text())
        document["services"]["app"]["environment"] = {"TOKEN": "changed"}
        tmp_instance.compose_path.write_text(yaml.safe_dump(document))
        return result

    runtime.compose = compose_and_change_environment

    with pytest.raises(
        AutoUpdateError,
        match="rollback failed.*Compose file changed",
    ):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.local_images[image] == "old"
    assert len(runtime.compose_calls) == 1
    assert runtime.compose_snapshots == [original_snapshot]


def test_failed_apply_restores_tags_and_verifies_rollback(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={image: "new"},
        compose_codes=[1, 0],
        compose_healths=["unhealthy", "healthy"],
    )

    with pytest.raises(AutoUpdateError, match="rollback succeeded"):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.local_images[image] == "old"
    assert runtime.containers["app"]["image_id"] == "old"
    assert len(runtime.compose_calls) == 2


def test_zero_exit_with_wrong_health_triggers_verified_rollback(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={image: "new"},
        compose_codes=[0, 0],
        compose_healths=["unhealthy", "healthy"],
    )

    with pytest.raises(AutoUpdateError, match="rollback succeeded"):
        update_instance(runtime, tmp_instance, "study")

    assert len(runtime.compose_calls) == 2
    assert runtime.containers["app"]["image_id"] == "old"


def test_failed_rollback_is_reported(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={image: "new"},
        compose_codes=[1, 1],
    )

    with pytest.raises(AutoUpdateError, match="rollback failed"):
        update_instance(runtime, tmp_instance, "study")


def test_digest_reference_never_pulls_or_retags_on_other_pull_failure(tmp_instance):
    digest = "docker.io/example/pinned@sha256:abc"
    moving = "docker.io/example/moving:latest"
    services = {"pinned": (digest, "digest-id"), "moving": (moving, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services, pull_failures={moving})

    with pytest.raises(AutoUpdateError, match="pull failed"):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.pull_calls == [moving]
    assert not any(call[0] == "tag" and call[-1] == digest for call in runtime.run_calls)


def test_pull_timeout_restores_tags_without_compose(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services, pull_timeouts={image})

    with pytest.raises(AutoUpdateError, match="timed out"):
        update_instance(runtime, tmp_instance, "study")

    assert runtime.compose_calls == []


def test_cleanup_failure_is_warning_data_not_transaction_failure(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(
        services,
        pulled={image: "new"},
        cleanup_failures={"old"},
    )

    result = update_instance(runtime, tmp_instance, "study", cleanup=True)

    assert result.changed_images == (image,)
    assert result.cleanup_errors == ("old: image is in use",)


def test_instance_lock_rejects_concurrent_update(tmp_instance):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services)

    with InstanceLock(tmp_instance):
        with pytest.raises(AutoUpdateError, match="locked by another process"):
            update_instance(runtime, tmp_instance, "study")

    assert runtime.run_calls == []


def test_scheduled_update_reloads_config_after_waiting_for_lock(
    tmp_instance, monkeypatch
):
    import src.core.auto_update as auto_update_core

    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    save_config(
        OpalConfig(stack_name="study", watchtower={"enabled": True}),
        tmp_instance,
    )
    runtime = FakeRuntime(services)

    class ConfigChangingLock:
        def __init__(self, instance, *, timeout_seconds=0):
            assert instance == tmp_instance
            assert timeout_seconds == 10

        def __enter__(self):
            save_config(OpalConfig(stack_name="changed"), tmp_instance)
            return self

        def __exit__(self, *exc_info):
            return None

    monkeypatch.setattr(auto_update_core, "InstanceLock", ConfigChangingLock)

    with pytest.raises(ScheduledUpdateDisabled, match="disabled while"):
        update_instance(
            runtime,
            tmp_instance,
            "study",
            lock_timeout_seconds=10,
            scheduled=True,
        )

    assert runtime.run_calls == []


def test_podman_path_never_invokes_a_docker_executable(tmp_instance, monkeypatch):
    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"Unexpected direct process invocation: {args}")
        ),
    )

    update_instance(runtime, tmp_instance, "study")

    assert all(call[0] not in {"docker", "docker-compose"} for call in runtime.run_calls)


def test_scheduled_command_skips_disabled_feature(tmp_instance, monkeypatch):
    from src.commands import auto_update as command

    save_config(OpalConfig(stack_name="study"), tmp_instance)
    monkeypatch.setattr(
        command,
        "get_runtime",
        lambda instance: (_ for _ in ()).throw(AssertionError("runtime probed")),
    )

    result = CliRunner().invoke(
        command.auto_update,
        ["--scheduled"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0
    assert "disabled" in result.output


def test_command_returns_nonzero_on_transaction_failure(tmp_instance, monkeypatch):
    from src.commands import auto_update as command

    image = "docker.io/example/app:latest"
    services = {"app": (image, "old")}
    _prepare(tmp_instance, services)
    runtime = FakeRuntime(services, pull_failures={image})
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    monkeypatch.setattr(command, "get_runtime", lambda instance: runtime)

    result = CliRunner().invoke(
        command.auto_update,
        [],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "pull failed" in result.output
