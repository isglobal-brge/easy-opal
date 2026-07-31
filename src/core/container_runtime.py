"""Container runtime selection and command execution."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.models.instance import InstanceContext
from src.utils.images import qualify_image

RUNTIME_NAMES = ("docker", "podman")
RUNTIME_CHOICES = ("auto", *RUNTIME_NAMES)
MIN_PODMAN = (4, 6, 0)
MIN_PODMAN_COMPOSE = (1, 6, 0)
_PORT_THRESHOLD_UNSET = object()


class RuntimeSelectionError(RuntimeError):
    """Raised when the requested container runtime cannot be used safely."""


@dataclass(frozen=True)
class ContainerRuntime:
    """A usable container engine and its Compose command."""

    name: str
    command: str
    compose_command: tuple[str, ...]
    env: dict[str, str] = field(default_factory=dict)

    def _env(self, override: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.env)
        if override:
            env.update(override)
        return env

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        """Run a container engine command."""
        kwargs["env"] = self._env(kwargs.get("env"))
        return subprocess.run([self.command, *args], **kwargs)

    def compose(
        self,
        args: list[str],
        instance: InstanceContext,
        project_name: str | None = None,
        compose_file: Path | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess:
        """Run Compose for an easy-opal instance."""
        if project_name is None:
            from src.core.config_manager import load_config

            project_name = load_config(instance).stack_name

        command = [
            *self.compose_command,
            "--project-name",
            project_name,
            "-f",
            str(compose_file or instance.compose_path),
            *args,
        ]
        kwargs["env"] = self._env(kwargs.get("env"))
        return subprocess.run(command, **kwargs)

    def pull(self, image: str, **kwargs: Any) -> subprocess.CompletedProcess:
        """Pull an image with this runtime."""
        kwargs.setdefault("check", False)
        return self.run(["pull", qualify_image(image)], **kwargs)


_requested_runtime: str | None = None


def set_requested_runtime(runtime: str) -> None:
    """Set the runtime choice for the current CLI invocation."""
    if runtime not in RUNTIME_CHOICES:
        choices = ", ".join(RUNTIME_CHOICES)
        raise ValueError(f"Invalid container runtime '{runtime}'. Choose one of: {choices}.")

    global _requested_runtime
    _requested_runtime = runtime


def _runtime_choice() -> str:
    choice = _requested_runtime
    if choice is None:
        choice = os.environ.get("EASY_OPAL_RUNTIME", "auto").strip().lower()
    if choice not in RUNTIME_CHOICES:
        choices = ", ".join(RUNTIME_CHOICES)
        raise RuntimeSelectionError(
            f"Invalid EASY_OPAL_RUNTIME value '{choice}'. Choose one of: {choices}."
        )
    return choice


def _definition(name: str) -> ContainerRuntime:
    env: dict[str, str] = {}
    if name == "podman":
        # Pin the independent provider. Without this, `podman compose` may
        # delegate to docker-compose and reintroduce a hidden Docker tool
        # dependency into an explicitly selected Podman setup.
        env["PODMAN_COMPOSE_PROVIDER"] = "podman-compose"
    return ContainerRuntime(
        name=name,
        command=name,
        compose_command=(name, "compose"),
        env=env,
    )


def _probe(command: list[str], env: dict[str, str]) -> tuple[bool, str, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=10,
        )
    except FileNotFoundError:
        return False, f"'{command[0]}' is not installed", ""
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(command)} timed out", ""
    except OSError as exc:
        return False, f"cannot execute '{command[0]}': {exc}", ""

    # Structured commands normally write their machine-readable value to
    # stdout. Ignore unrelated warnings on stderr unless stdout is empty.
    output = result.stdout if (result.stdout or "").strip() else (result.stderr or "")
    if result.returncode == 0:
        return True, "", output
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    suffix = f": {detail[-1]}" if detail else ""
    return (
        False,
        f"{' '.join(command)} failed with exit code {result.returncode}{suffix}",
        output,
    )


def _available_runtime(name: str) -> tuple[ContainerRuntime | None, str]:
    if name == "podman":
        provider = shutil.which("podman-compose")
        if not provider:
            return None, "Compose provider unavailable ('podman-compose' is not installed)"
        ok, detail, output = _probe([provider, "version"], os.environ.copy())
        if not ok:
            return None, f"Compose provider unavailable ({detail})"
        match = re.search(
            r"podman-compose(?:\s+version)?\s*:?[ ]*v?(\d+)\.(\d+)\.(\d+)",
            output,
            re.IGNORECASE,
        )
        if not match:
            return None, (
                "Compose provider version could not be determined from "
                f"{output.strip()!r}"
            )
        version = tuple(int(part) for part in match.groups())
        if version < MIN_PODMAN_COMPOSE:
            minimum = ".".join(str(part) for part in MIN_PODMAN_COMPOSE)
            found = ".".join(str(part) for part in version)
            return None, (
                f"Compose provider {found} is too old; podman-compose "
                f">={minimum} is required"
            )

    runtime = _definition(name)
    env = runtime._env()
    probes = [
        ([runtime.command, "--version"], "engine executable"),
        ([runtime.command, "ps"], "engine service"),
    ]
    if name == "podman":
        probes.append(
            (
                [
                    "podman",
                    "info",
                    "--format",
                    "{{.Version.Version}}",
                ],
                "engine host version",
            )
        )
    if name == "docker":
        probes.append(
            (
                [
                    "docker",
                    "version",
                    "--format",
                    "{{.Server.Platform.Name}}",
                ],
                "engine identity",
            )
        )
    probes.append(([*runtime.compose_command, "version"], "Compose provider"))
    probes.append(
        ([*runtime.compose_command, "up", "--help"], "Compose wait support")
    )

    for command, description in probes:
        ok, detail, output = _probe(command, env)
        if not ok:
            return None, f"{description} unavailable ({detail})"

        if description == "Compose wait support" and "--wait" not in output:
            return None, (
                "Compose provider does not support 'up --wait', which is "
                "required for health-aware startup"
            )
        if description == "Compose wait support" and "--wait-timeout" not in output:
            return None, (
                "Compose provider does not support 'up --wait-timeout', which is "
                "required for bounded health verification"
            )

        if name == "docker" and description == "engine identity":
            if "podman" in output.lower():
                return None, (
                    "engine identity is Podman behind a Docker-compatible CLI; "
                    "select --runtime podman"
                )
        if name == "podman" and description == "engine executable":
            match = re.search(
                r"\bpodman(?:\s+version)?\s+v?(\d+)\.(\d+)\.(\d+)",
                output,
                re.IGNORECASE,
            )
            if not match:
                return None, (
                    "engine version could not be determined from "
                    f"{output.strip()!r}"
                )
            version = tuple(int(part) for part in match.groups())
            if version < MIN_PODMAN:
                minimum = ".".join(str(part) for part in MIN_PODMAN)
                found = ".".join(str(part) for part in version)
                return None, (
                    f"engine {found} is too old; Podman >={minimum} is required"
                )
        if name == "podman" and description == "engine host version":
            match = re.fullmatch(
                r"\s*v?(\d+)\.(\d+)\.(\d+)(?:[-+][^\s]+)?\s*",
                output,
            )
            if not match:
                return None, (
                    "engine host version could not be determined from "
                    f"{output.strip()!r}"
                )
            version = tuple(int(part) for part in match.groups())
            if version < MIN_PODMAN:
                minimum = ".".join(str(part) for part in MIN_PODMAN)
                found = ".".join(str(part) for part in version)
                return None, (
                    f"engine host {found} is too old; Podman >={minimum} is required"
                )
    return runtime, ""


def _instance_binding(instance: InstanceContext | None) -> str | None:
    if instance is None:
        return None
    from src.core.instance_manager import get_instance_runtime

    return get_instance_runtime(instance)


def list_project_volumes(
    runtime: ContainerRuntime, project_name: str
) -> list[str]:
    """List Compose-owned volumes, raising when either label query fails."""
    names: set[str] = set()
    for project_label in (
        "com.docker.compose.project",
        "io.podman.compose.project",
    ):
        result = runtime.run(
            [
                "volume",
                "ls",
                "--filter",
                f"label={project_label}={project_name}",
                "--format",
                "{{.Name}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        result.check_returncode()
        names.update(name for name in result.stdout.splitlines() if name)
    return sorted(names)


def _runtime_has_instance(runtime: ContainerRuntime, instance: InstanceContext) -> bool:
    """Return whether an engine already owns resources for a legacy instance."""
    try:
        raw_config = json.loads(instance.config_path.read_text())
        project_name = raw_config.get("stack_name") or instance.name
    except (OSError, json.JSONDecodeError):
        project_name = instance.name

    try:
        for project_label in (
            "com.docker.compose.project",
            "io.podman.compose.project",
        ):
            containers = runtime.run(
                [
                    "ps",
                    "-a",
                    "--filter",
                    f"label={project_label}={project_name}",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if containers.returncode == 0 and containers.stdout.strip():
                return True

        if list_project_volumes(runtime, project_name):
            return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return False


def _persist_runtime(instance: InstanceContext, runtime: ContainerRuntime) -> None:
    from src.core.instance_manager import set_instance_runtime

    set_instance_runtime(instance, runtime.name)


def get_runtime(instance: InstanceContext | None = None) -> ContainerRuntime:
    """Resolve a complete engine + Compose pair, respecting instance binding."""
    choice = _runtime_choice()
    binding = _instance_binding(instance)

    if binding is not None and binding not in RUNTIME_NAMES:
        raise RuntimeSelectionError(
            f"Instance '{instance.name}' has invalid runtime binding '{binding}' in the registry."
        )

    if choice != "auto" and binding and choice != binding:
        raise RuntimeSelectionError(
            f"Instance '{instance.name}' is bound to {binding}, but {choice} was requested. "
            "Use the bound runtime. Moving an instance between engines is not automatic; "
            "create a new instance with the target runtime and transfer the required data. "
            "The backup command covers application and database data, not every ancillary "
            "service volume."
        )

    if binding:
        candidates = (binding,)
    elif choice != "auto":
        candidates = (choice,)
    else:
        candidates = RUNTIME_NAMES

    # Releases before runtime binding existed may already own containers or
    # volumes in either engine. Detect that ownership instead of silently
    # adopting Docker just because it is first in the auto preference order.
    if (
        instance is not None
        and binding is None
        and choice == "auto"
        and instance.config_path.exists()
    ):
        available: list[ContainerRuntime] = []
        failures: list[str] = []
        for name in candidates:
            runtime, failure = _available_runtime(name)
            if runtime is None:
                failures.append(f"{name}: {failure}")
            else:
                available.append(runtime)

        owners = [
            runtime
            for runtime in available
            if _runtime_has_instance(runtime, instance)
        ]
        if len(owners) == 1:
            _persist_runtime(instance, owners[0])
            return owners[0]
        if len(owners) > 1:
            names = ", ".join(runtime.name for runtime in owners)
            raise RuntimeSelectionError(
                f"Instance '{instance.name}' has resources in multiple runtimes "
                f"({names}). Select one explicitly with --runtime."
            )
        if available:
            raise RuntimeSelectionError(
                f"Instance '{instance.name}' has no runtime binding and its existing "
                "resources could not be identified. Select its runtime once with "
                "--runtime docker or --runtime podman."
            )

        detail = "; ".join(failures)
        raise RuntimeSelectionError(
            "No usable container runtime with Compose was found. "
            f"Install Docker with Compose or Podman with podman-compose. {detail}"
        )

    failures: list[str] = []
    for name in candidates:
        runtime, failure = _available_runtime(name)
        if runtime is None:
            failures.append(f"{name}: {failure}")
            continue

        if instance is not None and binding is None:
            _persist_runtime(instance, runtime)
        return runtime

    requested = binding or choice
    detail = "; ".join(failures)
    if requested == "auto":
        raise RuntimeSelectionError(
            "No usable container runtime with Compose was found. "
            f"Install Docker with Compose or Podman with podman-compose. {detail}"
        )
    raise RuntimeSelectionError(
        f"Container runtime '{requested}' is not usable with Compose. {detail}"
    )


def check_runtime(instance: InstanceContext | None = None) -> bool:
    """Return whether the selected runtime and Compose provider are usable."""
    try:
        get_runtime(instance)
    except RuntimeSelectionError:
        return False
    return True


def rootless_port_threshold(runtime: ContainerRuntime) -> int | None:
    """Return the first unprivileged host port for rootless Podman on Linux."""
    if runtime.name != "podman":
        return None

    # A remote Podman client reports the engine's rootless state, but this
    # process can only read the local kernel threshold.  Treat it as unknown
    # rather than validating remote ports against the wrong host.
    runtime_env = runtime._env()
    if runtime_env.get("CONTAINER_HOST") or runtime_env.get("CONTAINER_CONNECTION"):
        return None

    try:
        result = runtime.run(
            ["info", "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
        host = payload.get("host") or payload.get("Host")
        if not isinstance(host, dict):
            return None
        service_is_remote = host.get(
            "serviceIsRemote", host.get("ServiceIsRemote")
        )
        security = host.get("security") or host.get("Security")
        if service_is_remote is not False or not isinstance(security, dict):
            return None
        rootless = security.get("rootless", security.get("Rootless"))
        if rootless is not True:
            return None
        value = Path("/proc/sys/net/ipv4/ip_unprivileged_port_start").read_text()
        return int(value.strip())
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ):
        return None


def validate_runtime_config(
    runtime: ContainerRuntime,
    config,
    *,
    port_threshold: int | None | object = _PORT_THRESHOLD_UNSET,
) -> None:
    """Reject host-port settings that cannot run under rootless Podman."""
    threshold = (
        rootless_port_threshold(runtime)
        if port_threshold is _PORT_THRESHOLD_UNSET
        else port_threshold
    )
    if threshold is None or threshold <= 0:
        return

    strategy = getattr(config.ssl.strategy, "value", config.ssl.strategy)
    ports = {
        config.opal_http_port if strategy == "none" else config.opal_external_port
    }
    if strategy == "letsencrypt":
        ports.add(80)

    if config.flavor == "opal":
        ports.update(db.port for db in config.databases if not db.external)
        if config.agate.enabled and config.agate.mail_mode == "mailpit":
            ports.add(config.agate.mailpit_port)
    elif config.flavor == "armadillo" and config.keycloak.enabled:
        ports.add(config.keycloak.port)

    blocked = sorted(port for port in ports if port < threshold)
    if not blocked:
        return

    rendered = ", ".join(str(port) for port in blocked)
    raise RuntimeSelectionError(
        f"Rootless Podman cannot publish host port(s) {rendered} while "
        f"net.ipv4.ip_unprivileged_port_start={threshold}. Use ports at or "
        "above that threshold (for example --port 8443), configure the host "
        "for lower ports, or run rootful Podman. Behind a reverse proxy, use "
        "the 'none' or 'manual' SSL strategy instead of built-in HTTP-01."
    )
