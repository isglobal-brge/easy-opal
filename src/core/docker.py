"""Docker and Docker Compose operations."""

import subprocess
import sys

import yaml

from src.models.config import OpalConfig
from src.models.instance import InstanceContext
from src.services import ServiceRegistry
from src.core.secrets_manager import ensure_secrets
from src.utils.console import console, error, info


def check_docker() -> bool:
    """Verify Docker engine + daemon + Compose are available."""
    try:
        subprocess.run(["docker", "--version"], check=True, capture_output=True)
        subprocess.run(["docker", "ps"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        error("Docker is not installed or not running.")
        return False

    if not get_compose_cmd():
        error("Docker Compose is not available.")
        return False

    return True


def get_compose_cmd() -> list[str] | None:
    """Returns ['docker', 'compose'] or None. Requires Compose V2."""
    try:
        subprocess.run(
            ["docker", "compose", "version"], check=True, capture_output=True
        )
        return ["docker", "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        error("Docker Compose V2 is required. See https://docs.docker.com/compose/install/")
        return None


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
    """Run a docker compose command in the instance directory."""
    cmd = get_compose_cmd()
    if not cmd:
        error("Docker Compose is not available.")
        sys.exit(1)

    if project_name is None:
        from src.core.config_manager import load_config
        config = load_config(ctx)
        project_name = config.stack_name

    full_cmd = cmd + [
        "--project-name", project_name,
        "-f", str(ctx.compose_path),
        *args,
    ]

    console.print(f"[bold cyan]$ {' '.join(full_cmd)}[/bold cyan]")

    try:
        result = subprocess.run(full_cmd, check=False)
        if result.returncode != 0:
            error(f"Command failed with exit code {result.returncode}")
            return False
        return True
    except FileNotFoundError:
        error("Docker Compose command not found.")
        sys.exit(1)


def compose_up(ctx: InstanceContext, config: OpalConfig, wait: bool = True) -> bool:
    """Convergent up: regenerate compose, run up -d, optionally wait for health."""
    generate_compose(config, ctx)

    args = ["up", "-d", "--remove-orphans"]

    # Try --wait for health (Docker Compose V2.20+)
    if wait:
        args.append("--wait")

    ok = run_compose(args, ctx, config.stack_name)

    # If --wait failed (old compose version), fall back to basic up
    if not ok and wait:
        info("Retrying without --wait (older Docker Compose)...")
        ok = run_compose(["up", "-d", "--remove-orphans"], ctx, config.stack_name)

    return ok


def compose_down(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["down"], ctx, config.stack_name)


def compose_restart(ctx: InstanceContext, config: OpalConfig) -> bool:
    """Full restart: down then up."""
    compose_down(ctx, config)
    return compose_up(ctx, config)


def compose_status(ctx: InstanceContext, config: OpalConfig) -> bool:
    return run_compose(["ps"], ctx, config.stack_name)


def compose_reset(ctx: InstanceContext, config: OpalConfig) -> bool:
    """Stop and remove volumes."""
    return run_compose(["down", "-v"], ctx, config.stack_name)


def pull_image(image: str) -> bool:
    """Pull a Docker image with streamed output."""
    info(f"Pulling {image}...")
    try:
        result = subprocess.run(["docker", "pull", image], check=False)
        return result.returncode == 0
    except FileNotFoundError:
        error("Docker not found.")
        return False
