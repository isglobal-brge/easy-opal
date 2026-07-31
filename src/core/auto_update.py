"""Verified image updates with compensating rollback through the selected runtime."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.core.config_manager import config_exists, load_config
from src.core.container_runtime import ContainerRuntime
from src.core.instance_manager import InstanceLock
from src.models.instance import InstanceContext
from src.utils.images import qualify_image


PROJECT_LABELS = (
    "com.docker.compose.project",
    "io.podman.compose.project",
)
SERVICE_LABELS = (
    "com.docker.compose.service",
    "io.podman.compose.service",
)
ONE_OFF_LABELS = (
    "com.docker.compose.oneoff",
    "io.podman.compose.oneoff",
)


def _bounded_timeout(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


HEALTH_WAIT_SECONDS = _bounded_timeout(
    "EASY_OPAL_UPDATE_WAIT_SECONDS", 600, 10, 3600
)
PULL_TIMEOUT_SECONDS = _bounded_timeout(
    "EASY_OPAL_UPDATE_PULL_SECONDS", 1800, 30, 7200
)
COMPOSE_TIMEOUT_SECONDS = HEALTH_WAIT_SECONDS + 60
COMPOSE_UP_ARGS = (
    "up",
    "-d",
    "--force-recreate",
    "--no-deps",
    "--pull",
    "never",
    "--wait",
    "--wait-timeout",
    str(HEALTH_WAIT_SECONDS),
)


class AutoUpdateError(RuntimeError):
    """Raised when an update cannot complete safely."""


class StackNotRunningError(AutoUpdateError):
    """Raised when a scheduled update has no running stack to inspect."""


class ScheduledUpdateDisabled(AutoUpdateError):
    """Raised when an automatic-update job was disabled while waiting."""


@dataclass(frozen=True)
class AutoUpdateResult:
    """Summary of a successful update check."""

    pulled_images: tuple[str, ...]
    changed_images: tuple[str, ...]
    removed_image_ids: tuple[str, ...] = ()
    cleanup_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RunningStack:
    services: tuple[str, ...]
    service_references: dict[str, str]
    service_image_ids: dict[str, str]
    images: dict[str, str]


def _detail(result: Any) -> str:
    raw = getattr(result, "stderr", "") or getattr(result, "stdout", "") or ""
    if isinstance(raw, bytes):
        raw = raw.decode(errors="replace")
    rendered = str(raw).strip()
    return rendered or f"exit code {result.returncode}"


def _running_container_ids(
    runtime: ContainerRuntime, project_name: str
) -> list[str]:
    container_ids: set[str] = set()
    for label in PROJECT_LABELS:
        try:
            result = runtime.run(
                [
                    "ps",
                    "--filter",
                    f"label={label}={project_name}",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AutoUpdateError(
                f"Could not list running containers for '{project_name}': {exc}"
            ) from exc
        if result.returncode != 0:
            raise AutoUpdateError(
                f"Could not list running containers for '{project_name}': "
                f"{_detail(result)}"
            )
        container_ids.update(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )
    return sorted(container_ids)


def _json_records(output: str, action: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AutoUpdateError(f"{action}: runtime returned invalid JSON") from exc
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
        return parsed
    raise AutoUpdateError(f"{action}: runtime returned an unexpected JSON value")


def _canonical_reference(reference: str) -> str:
    rendered = qualify_image(reference)
    if "@" in rendered:
        return rendered
    final_component = rendered.rsplit("/", 1)[-1]
    if ":" not in final_component:
        rendered += ":latest"
    return rendered


def _read_compose_snapshot(instance: InstanceContext) -> bytes:
    try:
        return instance.compose_path.read_bytes()
    except OSError as exc:
        raise AutoUpdateError(f"Could not read the generated Compose file: {exc}") from exc


def _compose_service_images(
    instance: InstanceContext,
    compose_snapshot: bytes | None = None,
) -> dict[str, str]:
    if compose_snapshot is None:
        compose_snapshot = _read_compose_snapshot(instance)
    try:
        document = yaml.safe_load(compose_snapshot)
    except yaml.YAMLError as exc:
        raise AutoUpdateError(f"Could not read the generated Compose file: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("services"), dict):
        raise AutoUpdateError("Generated Compose file has no valid services mapping.")

    images: dict[str, str] = {}
    for service, definition in document["services"].items():
        if not isinstance(service, str) or not isinstance(definition, dict):
            raise AutoUpdateError("Generated Compose file has an invalid service definition.")
        reference = definition.get("image")
        if reference is not None:
            if not isinstance(reference, str) or not reference.strip():
                raise AutoUpdateError(
                    f"Compose service '{service}' has an invalid image reference."
                )
            images[service] = _canonical_reference(reference)
    return images


def _label_value(labels: dict[str, Any], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = labels.get(name)
        if value is not None:
            return str(value)
    return None


def _health_status(state: dict[str, Any]) -> str | None:
    health = state.get("Health") or state.get("Healthcheck")
    if not isinstance(health, dict):
        return None
    status = health.get("Status")
    return str(status).lower() if status else None


def _running_stack(
    runtime: ContainerRuntime,
    instance: InstanceContext,
    project_name: str,
    compose_snapshot: bytes | None = None,
) -> _RunningStack:
    container_ids = _running_container_ids(runtime, project_name)
    if not container_ids:
        raise StackNotRunningError(
            f"No running containers found for Compose project '{project_name}'."
        )

    compose_images = _compose_service_images(instance, compose_snapshot)

    try:
        result = runtime.run(
            ["inspect", *container_ids],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutoUpdateError(f"Could not inspect running containers: {exc}") from exc
    if result.returncode != 0:
        raise AutoUpdateError(
            f"Could not inspect running containers: {_detail(result)}"
        )

    service_image_ids: dict[str, set[str]] = {}
    service_references: dict[str, str] = {}
    for record in _json_records(result.stdout, "Could not inspect containers"):
        state = record.get("State") or {}
        if state.get("Running") is False:
            continue
        status = str(state.get("Status", "")).lower()
        if status and status != "running":
            continue

        config = record.get("Config") or {}
        labels = config.get("Labels") or record.get("Labels") or {}
        if not isinstance(labels, dict):
            raise AutoUpdateError(
                "Could not inspect running containers: labels have an invalid format"
            )
        one_off = _label_value(labels, ONE_OFF_LABELS)
        if one_off and one_off.lower() in {"1", "true", "yes"}:
            continue
        service = _label_value(labels, SERVICE_LABELS)
        if not service:
            raise AutoUpdateError(
                "Could not inspect running containers: Compose service label missing"
            )

        configured_reference = compose_images.get(service)
        if not configured_reference:
            raise AutoUpdateError(
                f"Running service '{service}' has no image in the current Compose file."
            )
        reference = config.get("Image") or record.get("ImageName")
        image_id = record.get("Image")
        if not reference or not image_id:
            raise AutoUpdateError(
                "Could not inspect running containers: image reference or ID missing"
            )
        running_reference = _canonical_reference(str(reference))
        if running_reference != configured_reference:
            raise AutoUpdateError(
                f"Running service '{service}' uses {running_reference}, but the current "
                f"Compose file specifies {configured_reference}. Run 'easy-opal up' "
                "before automatic updates."
            )
        health = _health_status(state)
        if health is not None and health != "healthy":
            raise AutoUpdateError(
                f"Running service '{service}' is {health}, not healthy."
            )
        service_references[service] = configured_reference
        service_image_ids.setdefault(service, set()).add(str(image_id))

    if not service_image_ids:
        raise AutoUpdateError(
            f"No running service images found for Compose project '{project_name}'."
        )

    ambiguous = {
        service: ids for service, ids in service_image_ids.items() if len(ids) != 1
    }
    if ambiguous:
        services = ", ".join(sorted(ambiguous))
        raise AutoUpdateError(
            "Replicas of these services use different image IDs: "
            f"{services}. Reconcile the stack before updating."
        )

    flattened_ids = {
        service: next(iter(ids))
        for service, ids in sorted(service_image_ids.items())
    }
    image_ids: dict[str, set[str]] = {}
    for service, reference in service_references.items():
        image_ids.setdefault(reference, set()).add(flattened_ids[service])
    inconsistent_references = {
        reference: ids for reference, ids in image_ids.items() if len(ids) != 1
    }
    if inconsistent_references:
        references = ", ".join(sorted(inconsistent_references))
        raise AutoUpdateError(
            "Running services use the same image reference with different IDs: "
            f"{references}. Reconcile the stack before updating."
        )

    return _RunningStack(
        services=tuple(sorted(flattened_ids)),
        service_references=dict(sorted(service_references.items())),
        service_image_ids=flattened_ids,
        images={
            reference: next(iter(ids))
            for reference, ids in sorted(image_ids.items())
        },
    )


def _restore_tags(
    runtime: ContainerRuntime, previous_images: dict[str, str]
) -> list[str]:
    failures: list[str] = []
    for reference, image_id in sorted(previous_images.items()):
        if "@" in reference:
            continue
        try:
            if _local_image_id(runtime, reference) == image_id:
                continue
        except AutoUpdateError:
            pass
        try:
            result = runtime.run(
                ["tag", image_id, reference],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{reference}: {exc}")
            continue
        if result.returncode != 0:
            failures.append(f"{reference}: {_detail(result)}")
    return failures


def _local_image_id(runtime: ContainerRuntime, reference: str) -> str:
    try:
        result = runtime.run(
            ["image", "inspect", reference],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutoUpdateError(f"Could not inspect pulled image {reference}: {exc}") from exc
    if result.returncode != 0:
        raise AutoUpdateError(
            f"Could not inspect pulled image {reference}: {_detail(result)}"
        )
    records = _json_records(result.stdout, f"Could not inspect image {reference}")
    if not records:
        raise AutoUpdateError(
            f"Could not inspect pulled image {reference}: no records returned"
        )
    image_id = records[0].get("Id") or records[0].get("ID")
    if not image_id:
        raise AutoUpdateError(f"Could not inspect pulled image {reference}: ID missing")
    return str(image_id)


def _pull_images(
    runtime: ContainerRuntime, previous_images: dict[str, str]
) -> dict[str, str]:
    pull_failures: list[str] = []
    for reference in previous_images:
        if "@" in reference:
            continue
        try:
            result = runtime.pull(
                reference,
                capture_output=True,
                text=True,
                timeout=PULL_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            pull_failures.append(f"{reference}: {exc}")
            continue
        if result.returncode != 0:
            pull_failures.append(f"{reference}: {_detail(result)}")

    if pull_failures:
        restore_failures = _restore_tags(runtime, previous_images)
        if restore_failures:
            raise AutoUpdateError(
                "Image pull failed and tag rollback failed. Pull errors: "
                f"{'; '.join(pull_failures)}. Tag errors: "
                f"{'; '.join(restore_failures)}"
            )
        raise AutoUpdateError(
            f"Image pull failed; previous tags restored: {'; '.join(pull_failures)}"
        )

    pulled_images: dict[str, str] = {}
    inspect_failures: list[str] = []
    for reference in previous_images:
        if "@" in reference:
            pulled_images[reference] = previous_images[reference]
            continue
        try:
            pulled_images[reference] = _local_image_id(runtime, reference)
        except AutoUpdateError as exc:
            inspect_failures.append(str(exc))
    if inspect_failures:
        restore_failures = _restore_tags(runtime, previous_images)
        suffix = (
            f" Tag rollback also failed: {'; '.join(restore_failures)}"
            if restore_failures
            else " Previous tags restored."
        )
        raise AutoUpdateError(
            f"Pulled image verification failed: {'; '.join(inspect_failures)}.{suffix}"
        )
    return pulled_images


def _compose_up(
    runtime: ContainerRuntime,
    instance: InstanceContext,
    project_name: str,
    services: tuple[str, ...],
    *,
    compose_file: Path,
) -> tuple[bool, str]:
    args = [*COMPOSE_UP_ARGS, *services]
    try:
        result = runtime.compose(
            args,
            instance,
            project_name=project_name,
            compose_file=compose_file,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMPOSE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, _detail(result)
    return True, ""


def _verify_stack(
    runtime: ContainerRuntime,
    instance: InstanceContext,
    project_name: str,
    expected: _RunningStack,
    expected_images: dict[str, str],
    compose_snapshot: bytes,
) -> tuple[bool, str]:
    try:
        observed = _running_stack(
            runtime,
            instance,
            project_name,
            compose_snapshot,
        )
    except AutoUpdateError as exc:
        return False, str(exc)

    if observed.services != expected.services:
        return False, (
            "running service set changed (expected "
            f"{', '.join(expected.services)}; found {', '.join(observed.services)})"
        )
    for service in expected.services:
        reference = expected.service_references[service]
        expected_id = expected_images[reference]
        observed_id = observed.service_image_ids[service]
        if observed_id != expected_id:
            return False, (
                f"service '{service}' uses image ID {observed_id}, expected {expected_id}"
            )
    return True, ""


def _cleanup_previous_images(
    runtime: ContainerRuntime,
    previous_images: dict[str, str],
    pulled_images: dict[str, str],
    changed_images: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    current_ids = set(pulled_images.values())
    old_ids = sorted(
        {
            previous_images[reference]
            for reference in changed_images
            if previous_images[reference] not in current_ids
        }
    )
    failures: list[str] = []
    removed: list[str] = []
    for image_id in old_ids:
        try:
            result = runtime.run(
                ["image", "rm", image_id],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{image_id}: {exc}")
            continue
        if result.returncode == 0:
            removed.append(image_id)
        else:
            failures.append(f"{image_id}: {_detail(result)}")
    return tuple(removed), tuple(failures)


def _validate_compose_snapshot(
    instance: InstanceContext,
    snapshot: bytes,
) -> tuple[bool, str]:
    try:
        current = _read_compose_snapshot(instance)
    except AutoUpdateError as exc:
        return False, str(exc)
    if current != snapshot:
        return False, "Generated Compose file changed during the update"
    return True, ""


@contextmanager
def _materialized_compose_snapshot(
    instance: InstanceContext,
    snapshot: bytes,
):
    """Expose an immutable-in-practice Compose snapshot to the CLI."""
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=".easy-opal-update-",
        suffix=".yml",
        dir=instance.root,
        delete=False,
    ) as handle:
        handle.write(snapshot)
        snapshot_path = Path(handle.name)
    try:
        yield snapshot_path
    finally:
        snapshot_path.unlink(missing_ok=True)


def _update_locked(
    runtime: ContainerRuntime,
    instance: InstanceContext,
    project_name: str,
    *,
    cleanup: bool,
) -> AutoUpdateResult:
    compose_snapshot = _read_compose_snapshot(instance)
    previous_stack = _running_stack(
        runtime,
        instance,
        project_name,
        compose_snapshot,
    )
    previous_images = previous_stack.images
    pulled_images = _pull_images(runtime, previous_images)
    changed_images = tuple(
        reference
        for reference in previous_images
        if previous_images[reference] != pulled_images[reference]
    )
    all_images = tuple(previous_images)
    if not changed_images:
        return AutoUpdateResult(all_images, ())

    compose_stable, compose_error = _validate_compose_snapshot(
        instance, compose_snapshot
    )
    if not compose_stable:
        restore_failures = _restore_tags(runtime, previous_images)
        suffix = (
            f" Tag rollback also failed: {'; '.join(restore_failures)}"
            if restore_failures
            else " Previous tags restored."
        )
        raise AutoUpdateError(f"{compose_error}.{suffix}")

    with _materialized_compose_snapshot(instance, compose_snapshot) as compose_file:
        applied, apply_error = _compose_up(
            runtime,
            instance,
            project_name,
            previous_stack.services,
            compose_file=compose_file,
        )
        if applied:
            compose_stable, compose_error = _validate_compose_snapshot(
                instance, compose_snapshot
            )
            if not compose_stable:
                applied, apply_error = False, compose_error
            else:
                applied, apply_error = _verify_stack(
                    runtime,
                    instance,
                    project_name,
                    previous_stack,
                    pulled_images,
                    compose_snapshot,
                )
        if not applied:
            tag_failures = _restore_tags(runtime, previous_images)
            details = []
            rolled_back = False
            rollback_error = ""
            if tag_failures:
                details.append("tag errors: " + "; ".join(tag_failures))
            else:
                compose_stable, compose_error = _validate_compose_snapshot(
                    instance, compose_snapshot
                )
                if not compose_stable:
                    rollback_error = compose_error
                else:
                    rolled_back, rollback_error = _compose_up(
                        runtime,
                        instance,
                        project_name,
                        previous_stack.services,
                        compose_file=compose_file,
                    )
                    if rolled_back:
                        rolled_back, rollback_error = _verify_stack(
                            runtime,
                            instance,
                            project_name,
                            previous_stack,
                            previous_images,
                            compose_snapshot,
                        )
            if not rolled_back:
                if rollback_error:
                    details.append("Compose/verification error: " + rollback_error)
                raise AutoUpdateError(
                    f"Updated stack failed verification ({apply_error}); rollback failed: "
                    + "; ".join(details)
                )
            raise AutoUpdateError(
                f"Updated stack failed verification ({apply_error}); rollback succeeded."
            )

    removed = ()
    cleanup_errors = ()
    if cleanup:
        removed, cleanup_errors = _cleanup_previous_images(
            runtime,
            previous_images,
            pulled_images,
            changed_images,
        )
    return AutoUpdateResult(
        all_images,
        changed_images,
        removed,
        cleanup_errors,
    )


def update_instance(
    runtime: ContainerRuntime,
    instance: InstanceContext,
    project_name: str,
    *,
    cleanup: bool = False,
    lock_timeout_seconds: float = 0,
    scheduled: bool = False,
) -> AutoUpdateResult:
    """Pull, apply, verify, and image-rollback one running Compose project."""
    try:
        with InstanceLock(instance, timeout_seconds=lock_timeout_seconds):
            if scheduled:
                if not config_exists(instance):
                    raise AutoUpdateError("No configuration found.")
                latest_config = load_config(instance)
                if not latest_config.watchtower.enabled:
                    raise ScheduledUpdateDisabled(
                        "Automatic updates were disabled while the job was waiting."
                    )
                project_name = latest_config.stack_name
                cleanup = latest_config.watchtower.cleanup
            return _update_locked(
                runtime,
                instance,
                project_name,
                cleanup=cleanup,
            )
    except AutoUpdateError:
        raise
    except (OSError, RuntimeError) as exc:
        raise AutoUpdateError(str(exc)) from exc
