"""Support bundle: collects diagnostics for debugging."""

import json
import platform
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import click

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.core.secrets_manager import load_secrets
from src.core.ssl import get_cert_info
from src.utils.console import console, success, error, info


def _redact(data: dict, keys_to_redact: set[str] | None = None) -> dict:
    """Recursively redact sensitive values from a dict."""
    redact = keys_to_redact or {"password", "secret", "token", "key"}
    result = {}
    for k, v in data.items():
        if any(r in k.lower() for r in redact):
            result[k] = "***REDACTED***"
        elif isinstance(v, dict):
            result[k] = _redact(v, redact)
        elif isinstance(v, list):
            result[k] = [_redact(i, redact) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


@click.command(name="support-bundle")
@click.option("-o", "--output", type=click.Path(), help="Output file path.")
@click.pass_context
def support_bundle(ctx, output):
    """Generate a support bundle for debugging."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"support-{cfg.stack_name}-{timestamp}"

    if output:
        zip_path = Path(output)
    else:
        zip_path = instance.root / f"{bundle_name}.zip"

    info(f"Generating support bundle: {bundle_name}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Redacted config
        redacted = _redact(cfg.model_dump())
        zf.writestr(f"{bundle_name}/config.json", json.dumps(redacted, indent=2))
        info("  Config (redacted)")

        # 2. Secrets summary (names only, no values)
        secrets = load_secrets(instance)
        secret_summary = {k: f"***({len(v)} chars)" for k, v in secrets.items()}
        zf.writestr(f"{bundle_name}/secrets-summary.json", json.dumps(secret_summary, indent=2))
        info("  Secrets summary")

        # 3. Compose file
        if instance.compose_path.exists():
            zf.write(instance.compose_path, f"{bundle_name}/docker-compose.yml")
            info("  Docker Compose")

        # 4. Certificate info
        cert_info = get_cert_info(instance)
        if cert_info:
            zf.writestr(f"{bundle_name}/cert-info.json", json.dumps(cert_info, indent=2))
            info("  Certificate info")

        # 5. Docker ps
        try:
            ps = subprocess.run(
                ["docker", "compose", "-f", str(instance.compose_path),
                 "--project-name", cfg.stack_name, "ps", "--format", "json"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            zf.writestr(f"{bundle_name}/docker-ps.txt", ps.stdout or "(no output)")
            info("  Container status")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            zf.writestr(f"{bundle_name}/docker-ps.txt", "(docker not available)")

        # 6. Container logs (last 50 lines each)
        for svc in ["mongo", "opal", "nginx", "rock"]:
            container = f"{cfg.stack_name}-{svc}"
            try:
                logs = subprocess.run(
                    ["docker", "logs", container, "--tail", "50"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                combined = (logs.stdout or "") + (logs.stderr or "")
                if combined.strip():
                    zf.writestr(f"{bundle_name}/logs-{svc}.txt", combined)
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        info("  Container logs")

        # 7. System info
        sys_info = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        }
        try:
            dv = subprocess.run(["docker", "--version"], capture_output=True, text=True, check=False)
            sys_info["docker"] = dv.stdout.strip()
            dcv = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, check=False)
            sys_info["compose"] = dcv.stdout.strip()
        except FileNotFoundError:
            pass
        zf.writestr(f"{bundle_name}/system-info.json", json.dumps(sys_info, indent=2))
        info("  System info")

    success(f"Bundle created: {zip_path}")
    info("Share this file when reporting issues. Passwords are redacted.")
