"""Host-native scheduling for unattended easy-opal maintenance jobs.

This module deliberately has no CLI or configuration integration.  It only
renders and manages launchd or systemd schedules for an instance.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn
from urllib.parse import unquote, urlsplit

from src.core.instance_manager import get_home
from src.models.instance import InstanceContext


class AutoUpdateScheduleError(RuntimeError):
    """Raised when a host schedule cannot be validated or managed safely."""


@dataclass(frozen=True)
class ScheduleFile:
    """A file that must be installed for a host schedule."""

    path: Path
    content: bytes
    mode: int = 0o600


@dataclass(frozen=True)
class AutoUpdateSchedulePlan:
    """Complete, non-mutating description of a proposed schedule."""

    backend: str
    identifier: str
    interval_hours: int
    command: tuple[str, ...]
    environment: Mapping[str, str]
    files: tuple[ScheduleFile, ...]


@dataclass(frozen=True)
class AutoUpdateScheduleStatus:
    """Installed and manager-visible state of an instance schedule."""

    backend: str
    identifier: str
    installed: bool
    enabled: bool
    active: bool
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class _RuntimeDetails:
    name: str
    command: str
    env: Mapping[str, str]


@dataclass(frozen=True)
class _ScheduleJob:
    """Stable identity and CLI arguments for one scheduled operation."""

    slug: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class _Backend:
    name: str
    identifier: str
    manager: str | None
    scope: str | None
    files: tuple[Path, ...]


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    kind: str
    content: bytes | None = None
    mode: int | None = None
    link_target: str | None = None


@dataclass(frozen=True)
class _ManagerState:
    loaded: bool
    enabled: bool
    active: bool


_SAFE_COMPONENT_RE = re.compile(r"[^a-z0-9_.-]+")
_CONTEXT_KEYS = {
    "docker": ("DOCKER_CONTEXT", "DOCKER_HOST"),
    "podman": ("CONTAINER_CONNECTION", "CONTAINER_HOST"),
}
_AUXILIARY_CONTEXT_KEYS = {
    "docker": (
        "DOCKER_CONFIG",
        "DOCKER_CERT_PATH",
        "DOCKER_TLS_VERIFY",
    ),
    "podman": (
        "CONTAINERS_CONF",
        "CONTAINERS_REGISTRIES_CONF",
        "CONTAINERS_STORAGE_CONF",
        "CONTAINER_SSHKEY",
        "PODMAN_CONNECTIONS_CONF",
        "PODMAN_NO_PAUSE_PROCESS",
        "REGISTRY_AUTH_FILE",
        "STORAGE_DRIVER",
        "STORAGE_OPTS",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    ),
}
_ROOT_CONTEXT_PATH_KEYS = {
    "docker": ("DOCKER_CONFIG", "DOCKER_CERT_PATH"),
    "podman": (
        "CONTAINERS_CONF",
        "CONTAINERS_REGISTRIES_CONF",
        "CONTAINERS_STORAGE_CONF",
        "CONTAINER_SSHKEY",
        "PODMAN_CONNECTIONS_CONF",
        "REGISTRY_AUTH_FILE",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    ),
}
_DOCKER_SYSTEM_PLUGIN_DIRS = (
    Path("/usr/local/lib/docker/cli-plugins"),
    Path("/usr/local/libexec/docker/cli-plugins"),
    Path("/usr/lib/docker/cli-plugins"),
    Path("/usr/libexec/docker/cli-plugins"),
)
_AUTO_UPDATE_JOB = _ScheduleJob("auto-update", ("auto-update", "--scheduled"))
_BACKUP_JOB = _ScheduleJob("backup", ("backup", "create", "--scheduled"))
_PROFILE_UPDATE_JOB = _ScheduleJob(
    "profile-update",
    ("profile", "pull", "--no-apply", "--scheduled"),
)


def _home_directory() -> Path:
    return Path.home()


def _root_home_directory() -> Path:
    """Return root's account home without trusting the caller's HOME."""
    try:
        home = Path(pwd.getpwuid(0).pw_dir)
    except (KeyError, OSError) as exc:
        raise AutoUpdateScheduleError(
            "Root's account home cannot be determined."
        ) from exc
    if not home.is_absolute():
        raise AutoUpdateScheduleError("Root's account home must be an absolute path.")
    return home


def _runtime_details(runtime: object) -> _RuntimeDetails:
    if isinstance(runtime, str):
        name = runtime
        command = runtime
        runtime_env: Mapping[str, str] = {}
    else:
        name = str(getattr(runtime, "name", ""))
        raw_command = getattr(runtime, "command", name)
        command = raw_command if isinstance(raw_command, str) else ""
        runtime_env = getattr(runtime, "env", {})

    if name not in _CONTEXT_KEYS:
        raise AutoUpdateScheduleError(
            f"Unsupported container runtime {name!r}; choose docker or podman."
        )
    if not command:
        raise AutoUpdateScheduleError(f"The {name} runtime has no executable.")
    if not isinstance(runtime_env, Mapping):
        raise AutoUpdateScheduleError(f"The {name} runtime environment is invalid.")
    return _RuntimeDetails(name=name, command=command, env=runtime_env)


def _unit_component(instance: InstanceContext) -> str:
    cleaned = _SAFE_COMPONENT_RE.sub("-", instance.name.lower()).strip(".-_")
    cleaned = (cleaned or "instance")[:40]
    identity = f"{instance.name}\0{instance.root.absolute()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned}-{digest}"


def _backend(
    instance: InstanceContext,
    job: _ScheduleJob = _AUTO_UPDATE_JOB,
) -> _Backend:
    component = _unit_component(instance)
    system = platform.system()

    if system == "Darwin":
        identifier = f"org.easyopal.{job.slug}.{component}"
        path = _home_directory() / "Library" / "LaunchAgents" / f"{identifier}.plist"
        return _Backend(
            name="launchd-user",
            identifier=identifier,
            manager=shutil.which("launchctl"),
            scope=f"gui/{os.getuid()}",
            files=(path,),
        )

    if system == "Linux":
        unit_base = f"easy-opal-{job.slug}-{component}"
        if os.getuid() == 0:
            directory = Path("/etc/systemd/system")
            backend_name = "systemd-system"
            scope = None
        else:
            config_home = os.environ.get("XDG_CONFIG_HOME")
            if config_home and not Path(config_home).expanduser().is_absolute():
                raise AutoUpdateScheduleError(
                    "XDG_CONFIG_HOME must be an absolute path."
                )
            directory = (
                Path(config_home).expanduser()
                if config_home
                else _home_directory() / ".config"
            ) / "systemd" / "user"
            backend_name = "systemd-user"
            scope = "user"
        return _Backend(
            name=backend_name,
            identifier=unit_base,
            manager=shutil.which("systemctl"),
            scope=scope,
            files=(
                directory / f"{unit_base}.service",
                directory / f"{unit_base}.timer",
            ),
        )

    raise AutoUpdateScheduleError(
        f"Automatic host scheduling is not supported on {system or 'this platform'}."
    )


def _validate_value(name: str, value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AutoUpdateScheduleError(f"{name} contains an unsafe control character.")
    return value


def _run(
    command: list[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=dict(env) if env is not None else None,
            cwd=cwd,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AutoUpdateScheduleError(
            f"Failed to execute {' '.join(command)}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail.splitlines()[-1]}" if detail else ""
        raise AutoUpdateScheduleError(
            f"{' '.join(command)} failed with exit code {result.returncode}{suffix}"
        )
    return result


def _manager_command(backend: _Backend, *args: str) -> list[str]:
    if backend.manager is None:
        executable = "launchctl" if backend.name == "launchd-user" else "systemctl"
        raise AutoUpdateScheduleError(
            f"{executable} is not installed or is not in PATH."
        )
    if backend.name == "systemd-system":
        manager = _assert_root_trusted_path(
            Path(backend.manager), "systemctl executable"
        )
        if not manager.is_file() or not os.access(manager, os.X_OK):
            raise AutoUpdateScheduleError("systemctl is not a runnable file.")
    if backend.name == "systemd-user":
        return [backend.manager, "--user", *args]
    return [backend.manager, *args]


def _check_manager(backend: _Backend) -> None:
    if backend.name == "launchd-user":
        assert backend.scope is not None
        _run(_manager_command(backend, "print", backend.scope))
    else:
        _run(_manager_command(backend, "show-environment"))


def _probe_environment(details: _RuntimeDetails) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in details.env.items():
        env[str(key)] = str(value)
    return env


def _context_environment(
    details: _RuntimeDetails, source: Mapping[str, str]
) -> dict[str, str]:
    context = {
        key: _validate_value(key, source[key])
        for key in (
            *_CONTEXT_KEYS[details.name],
            *_AUXILIARY_CONTEXT_KEYS[details.name],
        )
        if source.get(key)
    }
    for key in _ROOT_CONTEXT_PATH_KEYS[details.name]:
        if key in context and not Path(context[key]).expanduser().is_absolute():
            raise AutoUpdateScheduleError(f"{key} must be an absolute path.")
    return context


def _capture_context(
    details: _RuntimeDetails,
    *,
    probe_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    if probe_env is None:
        effective_probe_env = _probe_environment(details)
    else:
        effective_probe_env = dict(probe_env)
    context = _context_environment(details, effective_probe_env)
    if any(key in context for key in _CONTEXT_KEYS[details.name]):
        return context

    if details.name == "docker":
        result = _run(
            [details.command, "context", "show"],
            env=effective_probe_env,
            timeout=10,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 1:
            raise AutoUpdateScheduleError(
                "Docker's active context could not be determined safely."
            )
        context["DOCKER_CONTEXT"] = _validate_value("DOCKER_CONTEXT", lines[0])
        return context

    result = _run(
        [details.command, "system", "connection", "list", "--format", "json"],
        env=effective_probe_env,
        timeout=10,
    )
    try:
        connections = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise AutoUpdateScheduleError(
            "Podman's active connection could not be parsed."
        ) from exc
    if not isinstance(connections, list):
        raise AutoUpdateScheduleError("Podman's connection list has an invalid format.")
    if not all(isinstance(entry, dict) for entry in connections):
        raise AutoUpdateScheduleError("Podman's connection list has an invalid format.")
    defaults = [entry for entry in connections if entry.get("Default") is True]
    if len(defaults) > 1:
        raise AutoUpdateScheduleError("Podman reports multiple default connections.")
    if not defaults:
        if connections:
            raise AutoUpdateScheduleError(
                "Podman has connections but no stable default."
            )
        return context
    name = defaults[0].get("Name")
    if not isinstance(name, str) or not name:
        raise AutoUpdateScheduleError("Podman's default connection has no valid name.")
    context["CONTAINER_CONNECTION"] = _validate_value(
        "CONTAINER_CONNECTION", name
    )
    return context


def _capture_base_environment(backend: _Backend | None = None) -> dict[str, str]:
    system_root = backend is not None and backend.name == "systemd-system"
    home = _root_home_directory() if system_root else _home_directory().absolute()
    configured_home = os.environ.get("EASY_OPAL_HOME")
    if system_root and configured_home:
        easy_opal_path = Path(configured_home)
        if not easy_opal_path.is_absolute():
            raise AutoUpdateScheduleError(
                "EASY_OPAL_HOME must be an absolute path for a root schedule."
            )
    elif system_root:
        easy_opal_path = home / ".easy-opal"
    else:
        easy_opal_path = get_home().expanduser().absolute()
    easy_opal_home = str(easy_opal_path)
    path = os.environ.get("PATH") or os.defpath
    path = _validate_value("PATH", path)
    if any(
        not component or not Path(component).is_absolute()
        for component in path.split(os.pathsep)
    ):
        raise AutoUpdateScheduleError(
            "PATH must contain only non-empty absolute directories."
        )
    environment = {
        "EASY_OPAL_HOME": _validate_value("EASY_OPAL_HOME", easy_opal_home),
        "HOME": _validate_value("HOME", str(home)),
        "PATH": path,
        "PYTHONPATH": "",
    }
    if system_root:
        environment["PYTHONNOUSERSITE"] = "1"
    for key in (
        "EASY_OPAL_UPDATE_WAIT_SECONDS",
        "EASY_OPAL_UPDATE_PULL_SECONDS",
    ):
        if os.environ.get(key):
            environment[key] = _validate_value(key, os.environ[key])
    return environment


def _capture_environment(
    details: _RuntimeDetails,
    backend: _Backend | None = None,
) -> dict[str, str]:
    environment = _capture_base_environment(backend)
    environment.update(_capture_context(details))
    return environment


def _assert_root_trusted_path(path: Path, description: str) -> Path:
    lexical = path.expanduser()
    if not lexical.is_absolute():
        lexical = lexical.absolute()
    for candidate in (lexical, *lexical.parents):
        if not candidate.exists() and not candidate.is_symlink():
            continue
        metadata = candidate.lstat()
        writable = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        if metadata.st_uid != 0 or (
            not stat.S_ISLNK(metadata.st_mode) and writable
        ):
            raise AutoUpdateScheduleError(
                f"Refusing a root schedule because {description} has an "
                f"untrusted path component: {candidate}"
            )
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise AutoUpdateScheduleError(f"{description} is unavailable: {path}: {exc}") from exc
    for candidate in (resolved, *resolved.parents):
        metadata = candidate.stat()
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise AutoUpdateScheduleError(
                f"Refusing a root schedule because {description} is not "
                f"root-controlled: {candidate}"
            )
    return resolved


def _assert_root_trusted_tree(path: Path, description: str) -> Path:
    root = _assert_root_trusted_path(path, description)
    if not root.is_dir():
        raise AutoUpdateScheduleError(f"{description} is not a directory: {root}")
    pending = [root]
    visited: set[tuple[int, int]] = set()
    while pending:
        directory = pending.pop()
        metadata = directory.stat()
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in visited:
            continue
        visited.add(identity)
        try:
            children = tuple(directory.iterdir())
        except OSError as exc:
            raise AutoUpdateScheduleError(
                f"Cannot inspect trusted {description} tree at {directory}: {exc}"
            ) from exc
        for child in children:
            trusted = _assert_root_trusted_path(child, description)
            if trusted.is_dir():
                pending.append(trusted)
    return root


def _assert_root_trusted_executable(path: Path, description: str) -> Path:
    executable = _assert_root_trusted_path(path, description)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise AutoUpdateScheduleError(f"{description} is not a runnable file.")
    return executable


def _assert_root_trusted_socket(value: str, name: str) -> Path:
    parsed = urlsplit(_validate_value(name, value))
    if (
        parsed.scheme != "unix"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path
    ):
        raise AutoUpdateScheduleError(
            f"Root schedules only support local unix:// container endpoints; "
            f"{name}={value!r} is not allowed. Run easy-opal as the owner of a "
            "rootless or remote engine instead."
        )
    try:
        lexical = Path(unquote(parsed.path))
    except (TypeError, ValueError) as exc:
        raise AutoUpdateScheduleError(
            f"{name} contains an invalid socket path."
        ) from exc
    if not lexical.is_absolute():
        raise AutoUpdateScheduleError(f"{name} must contain an absolute socket path.")
    _assert_root_trusted_path(lexical.parent, f"{name} socket directory")
    if lexical.is_symlink() and lexical.lstat().st_uid != 0:
        raise AutoUpdateScheduleError(f"{name} socket symlink is not root-owned.")
    try:
        endpoint = lexical.resolve(strict=True)
        metadata = endpoint.stat()
    except (OSError, ValueError) as exc:
        raise AutoUpdateScheduleError(
            f"{name} socket is unavailable: {lexical}: {exc}"
        ) from exc
    _assert_root_trusted_path(endpoint.parent, f"{name} socket directory")
    if metadata.st_uid != 0 or not stat.S_ISSOCK(metadata.st_mode):
        raise AutoUpdateScheduleError(
            f"{name} must reference a root-owned Unix socket: {endpoint}"
        )
    return endpoint


def _docker_plugin_directories(environment: Mapping[str, str]) -> tuple[Path, ...]:
    config_directory = Path(
        environment.get("DOCKER_CONFIG", str(Path(environment["HOME"]) / ".docker"))
    )
    directories = [config_directory / "cli-plugins", *_DOCKER_SYSTEM_PLUGIN_DIRS]
    config_path = config_directory / "config.json"
    if not config_path.exists():
        return tuple(directories)
    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoUpdateScheduleError(
            f"Docker configuration could not be parsed safely: {config_path}"
        ) from exc
    extra = config.get("cliPluginsExtraDirs", []) if isinstance(config, dict) else []
    if not isinstance(extra, list) or not all(isinstance(item, str) for item in extra):
        raise AutoUpdateScheduleError(
            "Docker cliPluginsExtraDirs must be a list of absolute paths."
        )
    for item in extra:
        directory = Path(item)
        if not directory.is_absolute():
            raise AutoUpdateScheduleError(
                "Docker cliPluginsExtraDirs must contain only absolute paths."
            )
        directories.append(directory)
    return tuple(directories)


def _validate_root_context_paths(
    details: _RuntimeDetails, environment: Mapping[str, str]
) -> None:
    directory_keys = {
        "DOCKER_CONFIG",
        "DOCKER_CERT_PATH",
        "TMPDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    }
    if details.name == "podman" and environment.get("STORAGE_OPTS"):
        raise AutoUpdateScheduleError(
            "STORAGE_OPTS is not supported for a root-owned scheduled job; "
            "put trusted storage options in CONTAINERS_STORAGE_CONF instead."
        )
    for key in _ROOT_CONTEXT_PATH_KEYS[details.name]:
        value = environment.get(key)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            raise AutoUpdateScheduleError(
                f"{key} must be an absolute path for a root schedule."
            )
        if key in directory_keys:
            _assert_root_trusted_tree(path, key)
        else:
            trusted = _assert_root_trusted_path(path, key)
            if not trusted.is_file():
                raise AutoUpdateScheduleError(f"{key} is not a regular file: {trusted}")

    default_config = (
        Path(environment.get("DOCKER_CONFIG", Path(environment["HOME"]) / ".docker"))
        if details.name == "docker"
        else Path(
            environment.get(
                "XDG_CONFIG_HOME",
                str(Path(environment["HOME"]) / ".config"),
            )
        )
        / "containers"
    )
    if default_config.exists() or default_config.is_symlink():
        _assert_root_trusted_tree(default_config, f"{details.name} configuration")


def _validate_root_runtime_and_provider(
    details: _RuntimeDetails, environment: Mapping[str, str]
) -> None:
    runtime_path = shutil.which(details.command, path=environment["PATH"])
    if runtime_path is None:
        raise AutoUpdateScheduleError(
            f"The {details.name} executable is not available in the scheduled PATH."
        )
    _assert_root_trusted_executable(
        Path(runtime_path), f"{details.name} executable"
    )

    if details.name == "podman":
        provider = shutil.which("podman-compose", path=environment["PATH"])
        if provider is None:
            raise AutoUpdateScheduleError(
                "podman-compose is not available in the scheduled PATH."
            )
        _assert_root_trusted_executable(
            Path(provider), "podman-compose executable"
        )
        return

    for directory in _docker_plugin_directories(environment):
        if directory.exists() or directory.is_symlink():
            trusted_directory = _assert_root_trusted_tree(
                directory, "Docker CLI plugin directory"
            )
            provider = trusted_directory / "docker-compose"
            if provider.exists() or provider.is_symlink():
                _assert_root_trusted_executable(
                    provider, "Docker Compose provider"
                )


def _parse_single_endpoint(output: str, description: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AutoUpdateScheduleError(f"{description} returned an invalid endpoint.")
    try:
        value = json.loads(lines[0])
    except json.JSONDecodeError:
        value = lines[0]
    if not isinstance(value, str) or not value:
        raise AutoUpdateScheduleError(f"{description} returned an invalid endpoint.")
    return value


def _validate_system_context_endpoint(
    backend: _Backend,
    details: _RuntimeDetails,
    environment: Mapping[str, str],
) -> None:
    if backend.name != "systemd-system":
        return

    host_key = "DOCKER_HOST" if details.name == "docker" else "CONTAINER_HOST"
    if environment.get(host_key):
        _assert_root_trusted_socket(environment[host_key], host_key)

    if details.name == "docker":
        context = environment.get("DOCKER_CONTEXT")
        if not context:
            return
        result = _run(
            [
                details.command,
                "context",
                "inspect",
                context,
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
            env=environment,
            timeout=10,
        )
        endpoint = _parse_single_endpoint(result.stdout, "Docker context inspection")
        _assert_root_trusted_socket(endpoint, "Docker context endpoint")
        return

    connection = environment.get("CONTAINER_CONNECTION")
    if not connection:
        return
    result = _run(
        [details.command, "system", "connection", "list", "--format", "json"],
        env=environment,
        timeout=10,
    )
    try:
        connections = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise AutoUpdateScheduleError(
            "Podman's connection list could not be parsed safely."
        ) from exc
    matches = [
        entry
        for entry in connections
        if isinstance(entry, dict) and entry.get("Name") == connection
    ]
    if len(matches) != 1:
        raise AutoUpdateScheduleError(
            f"Podman connection {connection!r} could not be resolved uniquely."
        )
    endpoint = matches[0].get("URI") or matches[0].get("Uri")
    if not isinstance(endpoint, str) or not endpoint:
        raise AutoUpdateScheduleError(
            f"Podman connection {connection!r} has no valid endpoint."
        )
    _assert_root_trusted_socket(endpoint, "Podman connection endpoint")


def _validate_system_service_inputs(
    backend: _Backend,
    instance: InstanceContext,
    details: _RuntimeDetails,
    environment: Mapping[str, str],
) -> None:
    if backend.name != "systemd-system":
        return

    _assert_root_trusted_executable(Path(sys.executable), "Python executable")

    _assert_root_trusted_tree(
        Path(__file__).parent.parent, "easy-opal package"
    )

    easy_opal_home = _assert_root_trusted_path(
        Path(environment["EASY_OPAL_HOME"]), "EASY_OPAL_HOME"
    )
    home = _assert_root_trusted_path(Path(environment["HOME"]), "root HOME")
    if not home.is_dir():
        raise AutoUpdateScheduleError(f"Root HOME is not a directory: {home}")
    _assert_root_trusted_path(instance.root, "instance directory")
    for path, description in (
        (easy_opal_home / "registry.json", "instance registry"),
        (instance.config_path, "instance configuration"),
        (instance.compose_path, "generated Compose file"),
        (instance.secrets_path, "instance secrets"),
    ):
        if path.exists() or path.is_symlink():
            _assert_root_trusted_path(path, description)

    for component in environment["PATH"].split(os.pathsep):
        directory = _assert_root_trusted_path(Path(component), "PATH directory")
        if not directory.is_dir():
            raise AutoUpdateScheduleError(f"PATH entry is not a directory: {directory}")

    if backend.manager is None:
        raise AutoUpdateScheduleError("systemctl is not installed or is not in PATH.")
    _assert_root_trusted_executable(
        Path(backend.manager), "systemctl executable"
    )
    _validate_root_context_paths(details, environment)
    _validate_root_runtime_and_provider(details, environment)
    host_key = "DOCKER_HOST" if details.name == "docker" else "CONTAINER_HOST"
    if environment.get(host_key):
        _assert_root_trusted_socket(environment[host_key], host_key)


def _check_python_entrypoint(
    backend: _Backend,
    instance: InstanceContext,
    environment: Mapping[str, str],
) -> None:
    """Prove the scheduled interpreter resolves this installed easy-opal package."""
    working_directory = (
        Path("/") if backend.name == "systemd-system" else instance.root.absolute()
    )
    expected = (Path(__file__).resolve().parents[1] / "__init__.py").resolve()
    script = (
        "from pathlib import Path; import src; "
        "print(Path(src.__file__).resolve())"
    )
    result = _run(
        [
            sys.executable,
            *(("-I",) if backend.name == "systemd-system" else ()),
            "-c",
            script,
        ],
        env=environment,
        cwd=working_directory,
        timeout=10,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise AutoUpdateScheduleError(
            "The scheduled Python interpreter did not report one easy-opal package path."
        )
    try:
        observed = Path(lines[0]).resolve(strict=True)
    except OSError as exc:
        raise AutoUpdateScheduleError(
            f"The scheduled easy-opal package is unavailable: {lines[0]}"
        ) from exc
    if observed != expected:
        raise AutoUpdateScheduleError(
            "The scheduled Python interpreter resolves a different easy-opal "
            f"package ({observed}, expected {expected})."
        )


def _systemd_quote(value: str) -> str:
    value = _validate_value("systemd value", value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def _render_systemd(
    backend: _Backend,
    instance: InstanceContext,
    command: tuple[str, ...],
    environment: Mapping[str, str],
    interval_hours: int,
) -> tuple[ScheduleFile, ScheduleFile]:
    service_name = f"{backend.identifier}.service"
    working_directory = (
        Path("/") if backend.name == "systemd-system" else instance.root.absolute()
    )
    environment_lines = "\n".join(
        f"Environment={_systemd_quote(f'{key}={value}')}"
        for key, value in sorted(environment.items())
    )
    exec_start = " ".join(_systemd_quote(part) for part in command)
    service = (
        "[Unit]\n"
        f"Description=Easy Opal scheduled job for {backend.identifier}\n"
        "Wants=network-online.target\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={_systemd_quote(str(working_directory))}\n"
        "UMask=0077\n"
        f"{environment_lines}\n"
        f"ExecStart={exec_start}\n"
    ).encode("utf-8")
    timer = (
        "[Unit]\n"
        f"Description=Timer for {backend.identifier}\n\n"
        "[Timer]\n"
        f"OnActiveSec={interval_hours}h\n"
        f"OnUnitActiveSec={interval_hours}h\n"
        "Persistent=true\n"
        "AccuracySec=1min\n"
        f"Unit={service_name}\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    ).encode("utf-8")
    return (
        ScheduleFile(backend.files[0], service),
        ScheduleFile(backend.files[1], timer),
    )


def _render_launchd(
    backend: _Backend,
    instance: InstanceContext,
    command: tuple[str, ...],
    environment: Mapping[str, str],
    interval_hours: int,
) -> tuple[ScheduleFile, ...]:
    payload = {
        "Label": backend.identifier,
        "ProgramArguments": list(command),
        "WorkingDirectory": str(instance.root.absolute()),
        "EnvironmentVariables": dict(sorted(environment.items())),
        "StartInterval": interval_hours * 3600,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(instance.root / f".{backend.identifier}.log"),
        "StandardErrorPath": str(instance.root / f".{backend.identifier}.log"),
    }
    return (
        ScheduleFile(
            backend.files[0],
            plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True),
        ),
    )


def _nearest_existing_parent(path: Path) -> Path:
    candidate = path.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _check_write_access(files: tuple[Path, ...]) -> None:
    for path in files:
        parent = _nearest_existing_parent(path)
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            raise AutoUpdateScheduleError(
                f"Schedule directory is not writable: {path.parent}"
            )


def _preflight_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
    job: _ScheduleJob,
    *,
    extra_arguments: tuple[str, ...] = (),
) -> AutoUpdateSchedulePlan:
    if isinstance(interval_hours, bool) or not isinstance(interval_hours, int):
        raise AutoUpdateScheduleError(
            "Schedule interval must be a whole number of hours."
        )
    if interval_hours <= 0:
        raise AutoUpdateScheduleError("Schedule interval must be greater than zero.")
    if not instance.root.is_dir():
        raise AutoUpdateScheduleError(
            f"Instance directory does not exist: {instance.root}"
        )
    if (
        not sys.executable
        or not Path(sys.executable).is_absolute()
        or not os.access(sys.executable, os.X_OK)
    ):
        raise AutoUpdateScheduleError("The current Python executable is not runnable.")

    details = _runtime_details(runtime)
    backend = _backend(instance, job)
    _check_write_access(backend.files)
    environment = _capture_base_environment(backend)
    if backend.name == "systemd-system":
        raw_context = _context_environment(details, _probe_environment(details))
        environment.update(raw_context)
        # No external command may run as root until every executable and
        # inherited path used by the job has crossed the root-trust boundary.
        _validate_system_service_inputs(backend, instance, details, environment)
        environment.update(_capture_context(details, probe_env=environment))
        _validate_system_context_endpoint(backend, details, environment)
        _check_manager(backend)
    else:
        _check_manager(backend)
        environment.update(_capture_context(details))
    _check_python_entrypoint(backend, instance, environment)
    command = (
        sys.executable,
        *(("-I",) if backend.name == "systemd-system" else ()),
        "-m",
        "src",
        "--runtime",
        details.name,
        "-i",
        instance.name,
        *job.command,
        *extra_arguments,
    )
    if backend.name == "launchd-user":
        files = _render_launchd(
            backend, instance, command, environment, interval_hours
        )
    else:
        files = _render_systemd(
            backend, instance, command, environment, interval_hours
        )
    return AutoUpdateSchedulePlan(
        backend=backend.name,
        identifier=backend.identifier,
        interval_hours=interval_hours,
        command=command,
        environment=environment,
        files=files,
    )


def preflight_auto_update_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
    cleanup: bool = False,
) -> AutoUpdateSchedulePlan:
    """Validate and render an automatic-update schedule without host changes."""
    if not isinstance(cleanup, bool):
        raise AutoUpdateScheduleError("Update cleanup must be true or false.")
    arguments = ("--cleanup",) if cleanup else ()
    return _preflight_schedule(
        instance,
        runtime,
        interval_hours,
        _AUTO_UPDATE_JOB,
        extra_arguments=arguments,
    )


def preflight_backup_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
) -> AutoUpdateSchedulePlan:
    """Validate and render a scheduled-backup job without host changes."""
    return _preflight_schedule(instance, runtime, interval_hours, _BACKUP_JOB)


def preflight_profile_update_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
) -> AutoUpdateSchedulePlan:
    """Validate and render a profile-update job without host changes."""
    return _preflight_schedule(instance, runtime, interval_hours, _PROFILE_UPDATE_JOB)


def _atomic_write(artifact: ScheduleFile) -> bool:
    path = artifact.path
    if path.is_symlink():
        raise AutoUpdateScheduleError(f"Refusing to replace schedule symlink: {path}")
    if path.exists():
        try:
            if path.read_bytes() == artifact.content:
                path.chmod(artifact.mode)
                return False
        except OSError as exc:
            raise AutoUpdateScheduleError(
                f"Cannot read schedule file {path}: {exc}"
            ) from exc

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, artifact.mode)
            with os.fdopen(fd, "wb") as stream:
                stream.write(artifact.content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            Path(temporary).unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise AutoUpdateScheduleError(
            f"Cannot write schedule file {path}: {exc}"
        ) from exc
    return True


def _artifact_matches(artifact: ScheduleFile) -> bool:
    if artifact.path.is_symlink() or not artifact.path.exists():
        return False
    try:
        return (
            artifact.path.read_bytes() == artifact.content
            and stat.S_IMODE(artifact.path.stat().st_mode) == artifact.mode
        )
    except OSError as exc:
        raise AutoUpdateScheduleError(
            f"Cannot read schedule file {artifact.path}: {exc}"
        ) from exc


def _snapshot_files(paths: tuple[Path, ...]) -> tuple[_FileSnapshot, ...]:
    snapshots = []
    for path in paths:
        try:
            if path.is_symlink():
                snapshots.append(
                    _FileSnapshot(path, "symlink", link_target=os.readlink(path))
                )
            elif not path.exists():
                snapshots.append(_FileSnapshot(path, "absent"))
            elif path.is_file():
                snapshots.append(
                    _FileSnapshot(
                        path,
                        "file",
                        content=path.read_bytes(),
                        mode=stat.S_IMODE(path.stat().st_mode),
                    )
                )
            else:
                raise AutoUpdateScheduleError(
                    f"Schedule path is not a regular file: {path}"
                )
        except OSError as exc:
            raise AutoUpdateScheduleError(
                f"Cannot snapshot schedule file {path}: {exc}"
            ) from exc
    return tuple(snapshots)


def _remove_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise AutoUpdateScheduleError(
            f"Cannot remove schedule file {path}: {exc}"
        ) from exc


def _restore_file(snapshot: _FileSnapshot) -> None:
    if snapshot.kind == "absent":
        _remove_file(snapshot.path)
        return

    if snapshot.kind == "symlink":
        assert snapshot.link_target is not None
        _remove_file(snapshot.path)
        try:
            snapshot.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            snapshot.path.symlink_to(snapshot.link_target)
        except OSError as exc:
            raise AutoUpdateScheduleError(
                f"Cannot restore schedule symlink {snapshot.path}: {exc}"
            ) from exc
        return

    assert snapshot.kind == "file"
    assert snapshot.content is not None
    assert snapshot.mode is not None
    if snapshot.path.is_symlink():
        _remove_file(snapshot.path)
    _atomic_write(ScheduleFile(snapshot.path, snapshot.content, snapshot.mode))


def _launchd_loaded(backend: _Backend) -> bool:
    assert backend.scope is not None
    command = _manager_command(
        backend, "print", f"{backend.scope}/{backend.identifier}"
    )
    try:
        _run(command)
        return True
    except AutoUpdateScheduleError as exc:
        message = str(exc).lower()
        if "could not find service" in message or "service not found" in message:
            return False
        raise


def _systemd_properties(
    backend: _Backend,
    *,
    missing_ok: bool = False,
) -> dict[str, str]:
    timer_name = f"{backend.identifier}.timer"
    try:
        result = _run(
            _manager_command(
                backend,
                "show",
                timer_name,
                "--property=LoadState,UnitFileState,ActiveState",
            )
        )
    except AutoUpdateScheduleError as exc:
        message = str(exc).lower()
        if missing_ok and (
            "could not be found" in message
            or "not found" in message
            or "not loaded" in message
        ):
            return {
                "LoadState": "not-found",
                "UnitFileState": "disabled",
                "ActiveState": "inactive",
            }
        raise
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    expected = {"LoadState", "UnitFileState", "ActiveState"}
    if not expected.issubset(properties):
        raise AutoUpdateScheduleError(
            f"systemctl returned incomplete state for {timer_name}."
        )
    return properties


def _capture_manager_state(backend: _Backend) -> _ManagerState:
    if backend.name == "launchd-user":
        loaded = _launchd_loaded(backend)
        return _ManagerState(loaded, loaded, loaded)

    properties = _systemd_properties(backend, missing_ok=True)
    enabled = properties["UnitFileState"] in {
        "enabled",
        "enabled-runtime",
        "linked",
        "linked-runtime",
    }
    return _ManagerState(
        properties["LoadState"] != "not-found",
        enabled,
        properties["ActiveState"] not in {"inactive", "failed"},
    )


def _deactivate_manager(backend: _Backend, state: _ManagerState) -> None:
    if backend.name == "launchd-user":
        if not state.active:
            return
        assert backend.scope is not None
        _run(
            _manager_command(
                backend, "bootout", f"{backend.scope}/{backend.identifier}"
            )
        )
        return

    if state.enabled or state.active:
        _run(
            _manager_command(
                backend, "disable", "--now", f"{backend.identifier}.timer"
            )
        )


def _record_recovery(
    errors: list[str],
    description: str,
    operation: Callable[[], object],
) -> None:
    try:
        operation()
    except AutoUpdateScheduleError as exc:
        errors.append(f"{description}: {exc}")


def _restore_transaction(
    backend: _Backend,
    snapshots: tuple[_FileSnapshot, ...],
    previous_state: _ManagerState,
) -> list[str]:
    errors: list[str] = []
    try:
        current_state = _capture_manager_state(backend)
    except AutoUpdateScheduleError as exc:
        errors.append(f"inspect current manager state: {exc}")
        _record_recovery(
            errors,
            "force-deactivate replacement schedule",
            lambda: _deactivate_manager(backend, _ManagerState(True, True, True)),
        )
    else:
        _record_recovery(
            errors,
            "deactivate replacement schedule",
            lambda: _deactivate_manager(backend, current_state),
        )

    for snapshot in snapshots:
        _record_recovery(
            errors,
            f"restore {snapshot.path}",
            lambda snapshot=snapshot: _restore_file(snapshot),
        )

    if backend.name == "launchd-user":
        if previous_state.active:
            assert backend.scope is not None
            target = f"{backend.scope}/{backend.identifier}"
            _record_recovery(
                errors,
                "re-enable previous schedule",
                lambda: _run(_manager_command(backend, "enable", target)),
            )
            _record_recovery(
                errors,
                "reload previous schedule",
                lambda: _run(
                    _manager_command(
                        backend,
                        "bootstrap",
                        backend.scope,
                        str(backend.files[0]),
                    )
                ),
            )
        return errors

    _record_recovery(
        errors,
        "reload previous unit files",
        lambda: _run(_manager_command(backend, "daemon-reload")),
    )
    timer_name = f"{backend.identifier}.timer"
    if previous_state.enabled:
        arguments = ("enable", "--now", timer_name) if previous_state.active else (
            "enable",
            timer_name,
        )
        _record_recovery(
            errors,
            "restore previous timer state",
            lambda: _run(_manager_command(backend, *arguments)),
        )
    elif previous_state.active:
        _record_recovery(
            errors,
            "restart previous timer",
            lambda: _run(_manager_command(backend, "start", timer_name)),
        )
    return errors


def _raise_transaction_failure(
    error: AutoUpdateScheduleError,
    recovery_errors: list[str],
) -> NoReturn:
    if recovery_errors:
        detail = "; ".join(recovery_errors)
        raise AutoUpdateScheduleError(
            f"{error}. Recovery was incomplete: {detail}"
        ) from error
    raise error


def _schedule_status(
    instance: InstanceContext,
    job: _ScheduleJob,
) -> AutoUpdateScheduleStatus:
    backend = _backend(instance, job)
    installed = all(path.is_file() and not path.is_symlink() for path in backend.files)
    any_installed = any(path.exists() or path.is_symlink() for path in backend.files)
    if not any_installed:
        return AutoUpdateScheduleStatus(
            backend.name,
            backend.identifier,
            False,
            False,
            False,
            backend.files,
        )

    if backend.name == "launchd-user":
        loaded = _launchd_loaded(backend)
        enabled = loaded
        active = loaded
    else:
        properties = _systemd_properties(backend)
        enabled = properties["UnitFileState"] in {
            "enabled",
            "enabled-runtime",
            "linked",
            "linked-runtime",
        }
        active = properties["ActiveState"] == "active"

    return AutoUpdateScheduleStatus(
        backend.name,
        backend.identifier,
        installed,
        enabled,
        active,
        backend.files,
    )


def auto_update_schedule_status(
    instance: InstanceContext,
) -> AutoUpdateScheduleStatus:
    """Return the installed and manager-visible state of the update schedule."""
    return _schedule_status(instance, _AUTO_UPDATE_JOB)


def backup_schedule_status(instance: InstanceContext) -> AutoUpdateScheduleStatus:
    """Return the installed and manager-visible state of the backup schedule."""
    return _schedule_status(instance, _BACKUP_JOB)


def profile_update_schedule_status(
    instance: InstanceContext,
) -> AutoUpdateScheduleStatus:
    """Return the installed and manager-visible state of the profile schedule."""
    return _schedule_status(instance, _PROFILE_UPDATE_JOB)


def _install_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
    job: _ScheduleJob,
    *,
    extra_arguments: tuple[str, ...] = (),
) -> AutoUpdateScheduleStatus:
    plan = _preflight_schedule(
        instance,
        runtime,
        interval_hours,
        job,
        extra_arguments=extra_arguments,
    )
    backend = _backend(instance, job)
    snapshots = _snapshot_files(backend.files)
    previous_state = _capture_manager_state(backend)
    changed = any(not _artifact_matches(artifact) for artifact in plan.files)

    if not changed:
        status = _schedule_status(instance, job)
        if status.enabled and status.active:
            return status

    try:
        if backend.name == "launchd-user":
            assert backend.scope is not None
            _deactivate_manager(backend, previous_state)
            for artifact in plan.files:
                _atomic_write(artifact)
            _run(
                _manager_command(
                    backend,
                    "enable",
                    f"{backend.scope}/{backend.identifier}",
                )
            )
            _run(
                _manager_command(
                    backend, "bootstrap", backend.scope, str(backend.files[0])
                )
            )
        else:
            for artifact in plan.files:
                _atomic_write(artifact)
            _run(_manager_command(backend, "daemon-reload"))
            _run(
                _manager_command(
                    backend, "enable", "--now", f"{backend.identifier}.timer"
                )
            )

        status = _schedule_status(instance, job)
        if not status.installed or not status.enabled or not status.active:
            raise AutoUpdateScheduleError(
                f"{plan.identifier} was installed but is not active."
            )
        return status
    except AutoUpdateScheduleError as exc:
        recovery_errors = _restore_transaction(
            backend, snapshots, previous_state
        )
        _raise_transaction_failure(exc, recovery_errors)


def install_auto_update_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
    cleanup: bool = False,
) -> AutoUpdateScheduleStatus:
    """Install or update an automatic-update schedule idempotently."""
    if not isinstance(cleanup, bool):
        raise AutoUpdateScheduleError("Update cleanup must be true or false.")
    arguments = ("--cleanup",) if cleanup else ()
    return _install_schedule(
        instance,
        runtime,
        interval_hours,
        _AUTO_UPDATE_JOB,
        extra_arguments=arguments,
    )


def install_backup_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
) -> AutoUpdateScheduleStatus:
    """Install or update a backup schedule idempotently."""
    return _install_schedule(instance, runtime, interval_hours, _BACKUP_JOB)


def install_profile_update_schedule(
    instance: InstanceContext,
    runtime: object,
    interval_hours: int,
) -> AutoUpdateScheduleStatus:
    """Install or update a profile-update schedule idempotently."""
    return _install_schedule(instance, runtime, interval_hours, _PROFILE_UPDATE_JOB)


def _remove_schedule(instance: InstanceContext, job: _ScheduleJob) -> None:
    backend = _backend(instance, job)
    snapshots = _snapshot_files(backend.files)
    any_installed = any(snapshot.kind != "absent" for snapshot in snapshots)
    if not any_installed and backend.manager is None:
        return
    try:
        previous_state = _capture_manager_state(backend)
    except AutoUpdateScheduleError:
        # Disabled features must not require a working launchd/systemd session
        # when there are no schedule artifacts to remove. If files do exist,
        # retain them and surface the manager failure rather than risk leaving
        # an active job detached from its definition.
        if not any_installed:
            return
        raise
    if not any_installed and not previous_state.enabled and not previous_state.active:
        return

    try:
        _deactivate_manager(backend, previous_state)
        for path in backend.files:
            _remove_file(path)

        if backend.name != "launchd-user":
            _run(_manager_command(backend, "daemon-reload"))

        current_state = _capture_manager_state(backend)
        if current_state.enabled or current_state.active:
            raise AutoUpdateScheduleError(
                f"{backend.identifier} was removed but is still active."
            )
    except AutoUpdateScheduleError as exc:
        recovery_errors = _restore_transaction(
            backend, snapshots, previous_state
        )
        _raise_transaction_failure(exc, recovery_errors)


def remove_auto_update_schedule(instance: InstanceContext) -> None:
    """Remove an update schedule; repeated removal is a no-op."""
    _remove_schedule(instance, _AUTO_UPDATE_JOB)


def remove_backup_schedule(instance: InstanceContext) -> None:
    """Remove a backup schedule; repeated removal is a no-op."""
    _remove_schedule(instance, _BACKUP_JOB)


def remove_profile_update_schedule(instance: InstanceContext) -> None:
    """Remove a profile-update schedule; repeated removal is a no-op."""
    _remove_schedule(instance, _PROFILE_UPDATE_JOB)
