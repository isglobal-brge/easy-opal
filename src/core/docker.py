"""Container runtime: Docker or Podman, with Compose support."""

import subprocess
import sys

import yaml

from src.models.config import OpalConfig
from src.models.instance import InstanceContext
from src.services import ServiceRegistry
from src.core.secrets_manager import ensure_secrets
from src.utils.console import console, error, info, dim


def _detect_runtime() -> str | None:
    """Detect available container runtime: 'docker' or 'podman'."""
    for runtime in ("docker", "podman"):
        try:
            subprocess.run([runtime, "--version"], capture_output=True, check=True)
            subprocess.run([runtime, "ps"], capture_output=True, check=True)
            return runtime
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return None


def get_compose_cmd() -> list[str] | None:
    """Returns compose command: ['docker', 'compose'], ['podman', 'compose'], or None."""
    runtime = _detect_runtime()
    if not runtime:
        error("No container runtime found. Install Docker or Podman.")
        return None

    try:
        subprocess.run([runtime, "compose", "version"], capture_output=True, check=True)
        return [runtime, "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        error(f"{runtime} compose not available. Install Compose V2.")
        return None


def check_docker() -> bool:
    """Verify a container runtime + compose is available."""
    cmd = get_compose_cmd()
    if cmd:
        dim(f"Using: {' '.join(cmd)}")
        return True
    return False


def generate_compose(config: OpalConfig, ctx: InstanceContext) -> None:
    """Generate docker-compose.yml from the service registry."""
    secrets = ensure_secrets(ctx, config)
    registry = ServiceRegistry(config, ctx, secrets)
    compose = registry.assemble_compose()
    ctx.compose_path.write_text(yaml.dump(compose, default_flow_style=False, sort_keys=False))


def run_compose(
    args: list[str],
    ctx: InstanceContext,
    project_name: str | None = None,
) -> bool:
    """Run a compose command."""
    cmd = get_compose_cmd()
    if not cmd:
        sys.exit(1)

    if project_name is None:
        from src.core.config_manager import load_config
        config = load_config(ctx)
        project_name = config.stack_name

    full_cmd = cmd + ["--project-name", project_name, "-f", str(ctx.compose_path), *args]
    console.print(f"[bold cyan]$ {' '.join(full_cmd)}[/bold cyan]")

    try:
        result = subprocess.run(full_cmd, check=False)
        if result.returncode != 0:
            error(f"Command failed with exit code {result.returncode}")
            return False
        return True
    except FileNotFoundError:
        error("Compose command not found.")
        sys.exit(1)


def compose_up(ctx: InstanceContext, config: OpalConfig, wait: bool = True) -> bool:
    """Convergent up: regenerate compose, run up -d, optionally wait for health."""
    generate_compose(config, ctx)
    args = ["up", "-d", "--remove-orphans"]
    if wait:
        args.append("--wait")

    ok = run_compose(args, ctx, config.stack_name)
    if not ok and wait:
        info("Retrying without --wait...")
        ok = run_compose(["up", "-d", "--remove-orphans"], ctx, config.stack_name)
    return ok


def compose_down(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["down"], ctx, config.stack_name)


def compose_restart(ctx: InstanceContext, config: OpalConfig) -> bool:
    compose_down(ctx, config)
    return compose_up(ctx, config)


def compose_status(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["ps"], ctx, config.stack_name)


def compose_reset(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["down", "-v"], ctx, config.stack_name)


def pull_image(image: str) -> bool:
    """Pull an image using the detected runtime."""
    runtime = _detect_runtime() or "docker"
    info(f"Pulling {image}...")
    try:
        return subprocess.run([runtime, "pull", image], check=False).returncode == 0
    except FileNotFoundError:
        error(f"{runtime} not found.")
        return False
