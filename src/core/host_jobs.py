"""Reconcile host-side scheduled jobs for one easy-opal instance."""

from __future__ import annotations

import re
import subprocess

from src.core.auto_update_scheduler import (
    AutoUpdateScheduleError,
    install_auto_update_schedule,
    install_backup_schedule,
    install_profile_update_schedule,
    preflight_auto_update_schedule,
    preflight_backup_schedule,
    preflight_profile_update_schedule,
    remove_auto_update_schedule,
    remove_backup_schedule,
    remove_profile_update_schedule,
)
from src.core.container_runtime import ContainerRuntime
from src.models.config import OpalConfig
from src.models.instance import InstanceContext


_LEGACY_SOCKET_SIDECARS = ("backup", "profile-updater", "watchtower")
_COMPOSE_LABEL_PAIRS = (
    ("com.docker.compose.project", "com.docker.compose.service"),
    ("io.podman.compose.project", "io.podman.compose.service"),
)
_CONTAINER_ID_RE = re.compile(r"^[0-9a-fA-F]{12,64}$")


def _result_detail(result: subprocess.CompletedProcess) -> str:
    raw = result.stderr or result.stdout or ""
    if isinstance(raw, bytes):
        raw = raw.decode(errors="replace")
    return str(raw).strip() or f"exit code {result.returncode}"


def _remove_legacy_socket_sidecars(
    runtime: ContainerRuntime,
    project_name: str,
) -> tuple[str, ...]:
    """Remove only retired Compose sidecars that mounted the engine socket."""
    container_ids: set[str] = set()
    for project_label, service_label in _COMPOSE_LABEL_PAIRS:
        for service in _LEGACY_SOCKET_SIDECARS:
            try:
                result = runtime.run(
                    [
                        "ps",
                        "-a",
                        "--filter",
                        f"label={project_label}={project_name}",
                        "--filter",
                        f"label={service_label}={service}",
                        "--format",
                        "{{.ID}}",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AutoUpdateScheduleError(
                    f"Could not inspect legacy {service} containers: {exc}"
                ) from exc
            if result.returncode != 0:
                raise AutoUpdateScheduleError(
                    f"Could not inspect legacy {service} containers: "
                    f"{_result_detail(result)}"
                )
            stdout = result.stdout or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            for raw_id in stdout.splitlines():
                container_id = raw_id.strip()
                if not container_id:
                    continue
                if not _CONTAINER_ID_RE.fullmatch(container_id):
                    raise AutoUpdateScheduleError(
                        "Container runtime returned an invalid legacy sidecar ID."
                    )
                container_ids.add(container_id)

    if not container_ids:
        return ()

    ordered_ids = tuple(sorted(container_ids))
    try:
        result = runtime.run(
            ["rm", "-f", *ordered_ids],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutoUpdateScheduleError(
            f"Could not remove legacy socket sidecars: {exc}"
        ) from exc
    if result.returncode != 0:
        raise AutoUpdateScheduleError(
            "Could not remove legacy socket sidecars: "
            f"{_result_detail(result)}"
        )
    return ordered_ids


def preflight_enabled_schedules(
    instance: InstanceContext,
    runtime: ContainerRuntime,
    config: OpalConfig,
) -> None:
    """Validate all enabled schedules without changing host state."""
    if config.watchtower.enabled:
        preflight_auto_update_schedule(
            instance,
            runtime,
            config.watchtower.poll_interval_hours,
            cleanup=config.watchtower.cleanup,
        )
    if config.backup.enabled:
        preflight_backup_schedule(
            instance,
            runtime,
            config.backup.interval_hours,
        )
    if config.profile_updater.enabled:
        preflight_profile_update_schedule(
            instance,
            runtime,
            config.profile_updater.interval_hours,
        )


def reconcile_schedules(
    instance: InstanceContext,
    runtime: ContainerRuntime,
    config: OpalConfig,
) -> None:
    """Make installed schedules exactly match persisted configuration."""
    # Releases before host-native scheduling ran these jobs as privileged
    # Compose sidecars with the engine socket mounted. Retire only those exact
    # project/service-labelled containers before enabling their replacements.
    _remove_legacy_socket_sidecars(runtime, config.stack_name)

    if config.watchtower.enabled:
        install_auto_update_schedule(
            instance,
            runtime,
            config.watchtower.poll_interval_hours,
            cleanup=config.watchtower.cleanup,
        )
    else:
        remove_auto_update_schedule(instance)

    if config.backup.enabled:
        install_backup_schedule(
            instance,
            runtime,
            config.backup.interval_hours,
        )
    else:
        remove_backup_schedule(instance)

    if config.profile_updater.enabled:
        install_profile_update_schedule(
            instance,
            runtime,
            config.profile_updater.interval_hours,
        )
    else:
        remove_profile_update_schedule(instance)


def remove_all_schedules(instance: InstanceContext) -> None:
    """Remove every host-side job belonging to an instance."""
    remove_auto_update_schedule(instance)
    remove_backup_schedule(instance)
    remove_profile_update_schedule(instance)
