"""Compatibility wrappers around the selected container runtime."""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.models.config import OpalConfig
from src.models.instance import InstanceContext
from src.services import ServiceRegistry
from src.core.container_runtime import (
    RuntimeSelectionError,
    get_runtime,
    validate_runtime_config,
)
from src.core.secrets_manager import ensure_secrets
from src.utils.console import console, error, info, dim


@dataclass(frozen=True)
class CertificateAcquisitionResult:
    """Outcome plus the NGINX state that existed before the ACME challenge."""

    obtained: bool
    nginx_was_running: bool

    def __bool__(self) -> bool:
        return self.obtained


class CertificateAcquisitionError(RuntimeError):
    """ACME flow failure after the original NGINX state was observed."""

    def __init__(self, message: str, *, nginx_was_running: bool):
        super().__init__(message)
        self.nginx_was_running = nginx_was_running


def restore_running_nginx(config: OpalConfig, ctx: InstanceContext) -> bool:
    """Recreate only NGINX from the currently materialized configuration."""
    return run_compose(
        ["up", "-d", "--no-deps", "--force-recreate", "nginx"],
        ctx,
        config.stack_name,
    )


def _nginx_is_running(config: OpalConfig, ctx: InstanceContext) -> bool:
    runtime = get_runtime(ctx)
    container_ids: set[str] = set()
    for project_label, service_label in (
        ("com.docker.compose.project", "com.docker.compose.service"),
        ("io.podman.compose.project", "io.podman.compose.service"),
    ):
        try:
            state = runtime.run(
                [
                    "ps",
                    "--filter",
                    f"label={project_label}={config.stack_name}",
                    "--filter",
                    f"label={service_label}=nginx",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f"Could not determine the current NGINX state: {exc}"
            ) from exc
        if state.returncode != 0:
            detail = state.stderr or state.stdout or ""
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            suffix = f": {detail.strip()}" if detail.strip() else ""
            raise RuntimeError(
                "Could not determine the current NGINX state"
                f" (exit code {state.returncode}){suffix}"
            )
        stdout = state.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        for raw_id in stdout.splitlines():
            container_id = raw_id.strip()
            if not container_id:
                continue
            if not re.fullmatch(r"[0-9a-fA-F]{12,64}", container_id):
                raise RuntimeError(
                    "Container runtime returned an invalid NGINX container ID."
                )
            container_ids.add(container_id)
    return bool(container_ids)


def _atomic_write_private_text(path: Path, content: str) -> None:
    """Atomically replace a text file with permissions restricted to its owner."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _detect_runtime(ctx: InstanceContext | None = None) -> str | None:
    """Detect available container runtime: 'docker' or 'podman'."""
    try:
        return get_runtime(ctx).name
    except RuntimeSelectionError:
        return None


def get_compose_cmd(ctx: InstanceContext | None = None) -> list[str] | None:
    """Returns compose command: ['docker', 'compose'], ['podman', 'compose'], or None."""
    try:
        runtime = get_runtime(ctx)
    except RuntimeSelectionError as exc:
        error(str(exc))
        return None
    return list(runtime.compose_command)


def check_docker(ctx: InstanceContext | None = None) -> bool:
    """Verify a container runtime + compose is available."""
    try:
        runtime = get_runtime(ctx)
    except RuntimeSelectionError as exc:
        error(str(exc))
        return False
    dim(f"Using: {' '.join(runtime.compose_command)}")
    return True


def generate_compose(config: OpalConfig, ctx: InstanceContext) -> None:
    """Generate docker-compose.yml from the service registry."""
    runtime = get_runtime(ctx)
    validate_runtime_config(runtime, config)
    secrets = ensure_secrets(ctx, config)
    registry = ServiceRegistry(config, ctx, secrets, runtime_name=runtime.name)
    compose = registry.assemble_compose()
    rendered = yaml.dump(compose, default_flow_style=False, sort_keys=False)
    _atomic_write_private_text(ctx.compose_path, rendered)


def obtain_letsencrypt_certificate(
    config: OpalConfig, ctx: InstanceContext
) -> CertificateAcquisitionResult:
    """Run the ACME webroot flow without starting app dependencies."""
    nginx_was_running = _nginx_is_running(config, ctx)
    try:
        return _run_letsencrypt_challenge(
            config, ctx, nginx_was_running=nginx_was_running
        )
    except CertificateAcquisitionError:
        raise
    except Exception as exc:
        raise CertificateAcquisitionError(
            f"Let's Encrypt acquisition failed: {exc}",
            nginx_was_running=nginx_was_running,
        ) from exc


def _run_letsencrypt_challenge(
    config: OpalConfig,
    ctx: InstanceContext,
    *,
    nginx_was_running: bool,
) -> CertificateAcquisitionResult:
    from src.core.nginx import generate_nginx_config

    generate_nginx_config(config, ctx, acme_only=True)
    generate_compose(config, ctx)

    cert_ok = False
    nginx_stopped = False
    try:
        nginx_started = run_compose(
            ["up", "-d", "--no-deps", "--force-recreate", "nginx"],
            ctx,
            config.stack_name,
        )
        if nginx_started:
            certbot_args = [
                "--profile",
                "certbot",
                "run",
                "--rm",
                "certbot",
                "certonly",
                "--webroot",
                "--webroot-path",
                "/var/www/certbot",
                "--email",
                config.ssl.le_email,
                "--agree-tos",
                "--no-eff-email",
                "--force-renewal",
            ]
            for domain in config.hosts:
                certbot_args.extend(["-d", domain])
            cert_ok = run_compose(certbot_args, ctx, config.stack_name)
    finally:
        # `up` can fail after creating the container. Always make a bounded
        # best effort to stop the temporary challenge server.
        nginx_stopped = run_compose(["stop", "nginx"], ctx, config.stack_name)

    if not nginx_stopped:
        raise CertificateAcquisitionError(
            "The temporary ACME NGINX container could not be stopped and may "
            "still be running. Inspect the instance and run 'easy-opal down' "
            "before retrying.",
            nginx_was_running=nginx_was_running,
        )

    if cert_ok:
        generate_nginx_config(config, ctx, acme_only=False)
        generate_compose(config, ctx)
        if nginx_was_running and not restore_running_nginx(config, ctx):
            raise CertificateAcquisitionError(
                "The certificate was obtained, but the original running NGINX "
                "service could not be restored.",
                nginx_was_running=True,
            )
    return CertificateAcquisitionResult(cert_ok, nginx_was_running)


def run_compose(
    args: list[str],
    ctx: InstanceContext,
    project_name: str | None = None,
) -> bool:
    """Run a compose command."""
    if project_name is None:
        from src.core.config_manager import load_config
        config = load_config(ctx)
        project_name = config.stack_name

    try:
        runtime = get_runtime(ctx)
    except RuntimeSelectionError as exc:
        error(str(exc))
        return False

    full_cmd = [
        *runtime.compose_command,
        "--project-name",
        project_name,
        "-f",
        str(ctx.compose_path),
        *args,
    ]
    console.print(f"[bold cyan]$ {' '.join(full_cmd)}[/bold cyan]")

    try:
        result = runtime.compose(args, ctx, project_name=project_name, check=False)
        if result.returncode != 0:
            error(f"Command failed with exit code {result.returncode}")
            return False
        return True
    except FileNotFoundError:
        error("Compose command not found.")
        return False


def compose_up(ctx: InstanceContext, config: OpalConfig, wait: bool = True) -> bool:
    """Convergent up: regenerate compose + nginx, run up -d, optionally wait for health."""
    from src.core.nginx import generate_nginx_config
    generate_nginx_config(config, ctx)
    generate_compose(config, ctx)
    args = ["up", "-d", "--remove-orphans"]
    if wait:
        args.append("--wait")

    return run_compose(args, ctx, config.stack_name)


def compose_down(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["down", "--remove-orphans"], ctx, config.stack_name)


def compose_restart(ctx: InstanceContext, config: OpalConfig) -> bool:
    if not compose_down(ctx, config):
        return False
    return compose_up(ctx, config)


def compose_status(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["ps"], ctx, config.stack_name)


def compose_reset(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(
        ["down", "-v", "--remove-orphans"], ctx, config.stack_name
    )


def pull_image(image: str, ctx: InstanceContext | None = None) -> bool:
    """Pull an image using the selected runtime."""
    info(f"Pulling {image}...")
    try:
        runtime = get_runtime(ctx)
        return runtime.pull(image).returncode == 0
    except RuntimeSelectionError as exc:
        error(str(exc))
        return False
    except FileNotFoundError:
        error("Container runtime not found.")
        return False
