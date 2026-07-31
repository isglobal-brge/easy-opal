"""Migration checks for runtime-neutral host jobs."""

import subprocess

import pytest

from src.core import host_jobs
from src.models.config import OpalConfig


class LegacySidecarRuntime:
    name = "podman"

    def __init__(self):
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append(args)
        if args[0] == "rm":
            return subprocess.CompletedProcess(args, 0, "", "")

        filters = [
            args[index + 1]
            for index, value in enumerate(args)
            if value == "--filter"
        ]
        if "label=com.docker.compose.service=watchtower" in filters:
            stdout = "a" * 12 + "\n"
        elif "label=io.podman.compose.service=profile-updater" in filters:
            stdout = "b" * 12 + "\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout, "")


def test_legacy_socket_sidecars_are_removed_by_exact_compose_labels():
    runtime = LegacySidecarRuntime()

    removed = host_jobs._remove_legacy_socket_sidecars(runtime, "study")

    assert removed == ("a" * 12, "b" * 12)
    assert runtime.calls[-1] == ["rm", "-f", "a" * 12, "b" * 12]
    queries = runtime.calls[:-1]
    assert len(queries) == 6
    assert all("label=com.docker.compose.project=study" in call or
               "label=io.podman.compose.project=study" in call for call in queries)


def test_invalid_legacy_container_id_fails_closed():
    runtime = LegacySidecarRuntime()

    def invalid_run(args, **kwargs):
        runtime.calls.append(args)
        return subprocess.CompletedProcess(args, 0, "not-a-container-id\n", "")

    runtime.run = invalid_run

    with pytest.raises(
        host_jobs.AutoUpdateScheduleError, match="invalid legacy sidecar ID"
    ):
        host_jobs._remove_legacy_socket_sidecars(runtime, "study")

    assert all(call[0] == "ps" for call in runtime.calls)


def test_schedule_reconciliation_retires_sidecars_before_host_jobs(
    tmp_instance, monkeypatch
):
    events = []
    runtime = LegacySidecarRuntime()
    monkeypatch.setattr(
        host_jobs,
        "_remove_legacy_socket_sidecars",
        lambda *_args: events.append("legacy") or (),
    )
    monkeypatch.setattr(
        host_jobs,
        "remove_auto_update_schedule",
        lambda *_args: events.append("auto-update"),
    )
    monkeypatch.setattr(
        host_jobs,
        "remove_backup_schedule",
        lambda *_args: events.append("backup"),
    )
    monkeypatch.setattr(
        host_jobs,
        "remove_profile_update_schedule",
        lambda *_args: events.append("profile-update"),
    )

    host_jobs.reconcile_schedules(
        tmp_instance, runtime, OpalConfig(stack_name="study")
    )

    assert events == ["legacy", "auto-update", "backup", "profile-update"]
