"""Opt-in real Docker <-> Podman core backup/restore portability smoke."""

import os
import subprocess
import time
import uuid

import pytest
from click.testing import CliRunner

from src.cli import main
from src.core.config_manager import save_config
from src.core.container_runtime import get_runtime, set_requested_runtime
from src.core.instance_manager import create_instance, set_instance_runtime
from src.models.config import OpalConfig


pytestmark = pytest.mark.skipif(
    os.environ.get("EASY_OPAL_CROSS_RUNTIME") != "1",
    reason="requires real Docker and Podman engines",
)


def _checked(runtime, args, **kwargs):
    return runtime.run(args, check=True, **kwargs)


def _wait_for_mongo(runtime, container: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = runtime.run(
            [
                "exec",
                container,
                "mongosh",
                "--quiet",
                "--eval",
                "print(db.adminCommand('ping').ok)",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "1":
            return
        time.sleep(1)
    raise RuntimeError(f"MongoDB container '{container}' did not become ready")


def _create_stack(runtime, stack: str) -> tuple[list[str], list[str]]:
    mongo = f"{stack}-mongo"
    opal = f"{stack}-opal"
    mongo_volume = f"{stack}-mongo-data"
    opal_volume = f"{stack}-opal-data"
    containers = [mongo, opal]
    volumes = [mongo_volume, opal_volume]
    try:
        _checked(
            runtime,
            [
                "run",
                "-d",
                "--name",
                mongo,
                "-v",
                f"{mongo_volume}:/data/db",
                "docker.io/library/mongo:7.0",
            ],
            stdout=subprocess.DEVNULL,
        )
        _checked(
            runtime,
            [
                "run",
                "-d",
                "--name",
                opal,
                "-v",
                f"{opal_volume}:/srv",
                "docker.io/library/alpine:3.20",
                "sh",
                "-c",
                "sleep 3600",
            ],
            stdout=subprocess.DEVNULL,
        )
        _checked(
            runtime,
            ["exec", "--user", "0", opal, "chown", "1000:1000", "/srv"],
        )
        _wait_for_mongo(runtime, mongo)
    except Exception:
        runtime.run(["rm", "-f", "-v", *containers], check=False)
        runtime.run(["volume", "rm", "-f", *volumes], check=False)
        raise
    return containers, volumes


def _write_sentinels(runtime, stack: str, value: str) -> None:
    _checked(
        runtime,
        [
            "exec",
            f"{stack}-opal",
            "sh",
            "-c",
            "printf '%s' \"$1\" > /srv/runtime-sentinel",
            "easy-opal-migration",
            value,
        ],
    )
    expression = (
        "db.getSiblingDB('easy_opal_migration').sentinels.replaceOne("
        f"{{_id:'sentinel'}},{{value:'{value}'}},{{upsert:true}})"
    )
    _checked(
        runtime,
        [
            "exec",
            f"{stack}-mongo",
            "mongosh",
            "--quiet",
            "--eval",
            expression,
        ],
        stdout=subprocess.DEVNULL,
    )


def _assert_sentinels(runtime, stack: str, expected: str) -> None:
    application = _checked(
        runtime,
        ["exec", f"{stack}-opal", "cat", "/srv/runtime-sentinel"],
        capture_output=True,
        text=True,
    )
    assert application.stdout == expected

    database = _checked(
        runtime,
        [
            "exec",
            f"{stack}-mongo",
            "mongosh",
            "--quiet",
            "--eval",
            (
                "print(db.getSiblingDB('easy_opal_migration').sentinels."
                "findOne({_id:'sentinel'}).value)"
            ),
        ],
        capture_output=True,
        text=True,
    )
    assert database.stdout.strip() == expected


def _assert_application_owner_and_writable(runtime, stack: str) -> None:
    owner = _checked(
        runtime,
        [
            "exec",
            f"{stack}-opal",
            "stat",
            "-c",
            "%u:%g",
            "/srv/runtime-sentinel",
        ],
        capture_output=True,
        text=True,
    )
    assert owner.stdout.strip() == "1000:1000"
    _checked(
        runtime,
        [
            "exec",
            "--user",
            "1000",
            f"{stack}-opal",
            "sh",
            "-c",
            "printf writable > /srv/non-root-write-check",
        ],
    )


def _create_instance(name: str, runtime_name: str):
    instance = create_instance(name)
    save_config(
        OpalConfig(
            stack_name=name,
            mongo_version="7.0",
            ssl={"strategy": "none"},
        ),
        instance,
    )
    set_instance_runtime(instance, runtime_name)
    return instance


def test_backup_restore_transfers_core_data_in_both_directions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EASY_OPAL_HOME", str(tmp_path / "easy-opal-home"))
    token = uuid.uuid4().hex[:8]
    source_name = f"migration-docker-{token}"
    podman_name = f"migration-podman-{token}"
    final_name = f"migration-final-{token}"

    set_requested_runtime("docker")
    docker = get_runtime()
    set_requested_runtime("podman")
    podman = get_runtime()

    resources = []
    try:
        for runtime, name in (
            (docker, source_name),
            (podman, podman_name),
            (docker, final_name),
        ):
            containers, volumes = _create_stack(runtime, name)
            resources.append((runtime, containers, volumes))
            _create_instance(name, runtime.name)

        _write_sentinels(docker, source_name, "docker-source")
        _write_sentinels(podman, podman_name, "stale-podman")
        _write_sentinels(docker, final_name, "stale-docker")

        runner = CliRunner()
        docker_backup = tmp_path / "docker-source.tar.gz"
        result = runner.invoke(
            main,
            [
                "--runtime",
                "docker",
                "-i",
                source_name,
                "backup",
                "create",
                "-o",
                str(docker_backup),
            ],
        )
        assert result.exit_code == 0, result.output

        result = runner.invoke(
            main,
            [
                "--runtime",
                "podman",
                "-i",
                podman_name,
                "backup",
                "restore",
                str(docker_backup),
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        _assert_sentinels(podman, podman_name, "docker-source")
        _assert_application_owner_and_writable(podman, podman_name)

        # Prove the return trip transports data written while Podman owns the
        # target, instead of merely relaying the original Docker payload.
        _write_sentinels(podman, podman_name, "podman-source")

        podman_backup = tmp_path / "podman-source.tar.gz"
        result = runner.invoke(
            main,
            [
                "--runtime",
                "podman",
                "-i",
                podman_name,
                "backup",
                "create",
                "-o",
                str(podman_backup),
            ],
        )
        assert result.exit_code == 0, result.output

        result = runner.invoke(
            main,
            [
                "--runtime",
                "docker",
                "-i",
                final_name,
                "backup",
                "restore",
                str(podman_backup),
                "--yes",
            ],
        )
        assert result.exit_code == 0, result.output
        _assert_sentinels(docker, final_name, "podman-source")
        _assert_application_owner_and_writable(docker, final_name)
    finally:
        for runtime, containers, volumes in reversed(resources):
            runtime.run(["rm", "-f", "-v", *containers], check=False)
            runtime.run(["volume", "rm", "-f", *volumes], check=False)
