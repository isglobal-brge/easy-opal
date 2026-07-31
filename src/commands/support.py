"""Support bundle: collects diagnostics for debugging."""

import json
import os
import platform
import re
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import click
import yaml

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.core.secrets_manager import load_secrets
from src.core.ssl import get_cert_info
from src.utils.console import success, error, info, require_single_instance


_REDACTED = "***REDACTED***"
_SENSITIVE_KEYS = {"password", "secret", "token", "key"}
_COMPOSE_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:password|passwd|secret|token|credential|"
    r"api[_-]?key|private[_-]?key)s?(?:$|[_-])"
)


def _redact(data: dict, keys_to_redact: set[str] | None = None) -> dict:
    """Recursively redact sensitive values from a dict."""
    redact = keys_to_redact or _SENSITIVE_KEYS
    result = {}
    for k, v in data.items():
        if any(r in k.lower() for r in redact):
            result[k] = _REDACTED
        elif isinstance(v, dict):
            result[k] = _redact(v, redact)
        elif isinstance(v, list):
            result[k] = [_redact(i, redact) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


def _redact_known_secrets(text: str, secret_values) -> str:
    """Remove exact, non-empty secret values without reprocessing replacements."""
    values = sorted(
        {value for value in secret_values if isinstance(value, str) and value},
        key=len,
        reverse=True,
    )
    if not values:
        return text
    pattern = re.compile("|".join(re.escape(value) for value in values))
    return pattern.sub(_REDACTED, text)


def _redact_environment(environment):
    """Keep environment variable names while removing every supplied value."""
    if isinstance(environment, dict):
        return {name: _REDACTED for name in environment}
    if isinstance(environment, list):
        redacted = []
        for item in environment:
            if isinstance(item, str) and "=" in item:
                name, _, _value = item.partition("=")
                redacted.append(f"{name}={_REDACTED}")
            elif isinstance(item, str):
                redacted.append(item)
            else:
                redacted.append(_REDACTED)
        return redacted
    return _REDACTED


def _is_sensitive_compose_key(key: object) -> bool:
    lowered = str(key).lower()
    return lowered == "key" or _COMPOSE_SENSITIVE_KEY.search(lowered) is not None


def _redact_compose(data, secret_values):
    """Redact Compose credentials while retaining useful service structure."""
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            lowered = str(key).lower()
            if lowered == "environment":
                result[key] = _redact_environment(value)
            elif lowered == "env_file" or _is_sensitive_compose_key(key):
                result[key] = _REDACTED
            else:
                result[key] = _redact_compose(value, secret_values)
        return result
    if isinstance(data, list):
        return [_redact_compose(value, secret_values) for value in data]
    if isinstance(data, str):
        return _redact_known_secrets(data, secret_values)
    return data


def _render_redacted_compose(path: Path, secrets: dict[str, str]) -> str:
    compose = yaml.safe_load(path.read_text())
    if not isinstance(compose, dict):
        raise ValueError("Compose file must contain a mapping")
    redacted = _redact_compose(compose, secrets.values())
    return yaml.safe_dump(redacted, default_flow_style=False, sort_keys=False)


@contextmanager
def _private_zip_file(destination: Path):
    """Build a mode-0600 ZIP beside its destination and publish it atomically."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            yield archive
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


@click.command(name="support-bundle")
@click.option("-o", "--output", type=click.Path(), help="Output file path.")
@click.pass_context
def support_bundle(ctx, output):
    """Generate a support bundle for debugging."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)
    try:
        runtime = get_runtime(instance)
        runtime_error = None
    except RuntimeSelectionError as exc:
        runtime = None
        runtime_error = str(exc)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"support-{cfg.stack_name}-{timestamp}"

    if output:
        zip_path = Path(output)
    else:
        zip_path = instance.root / f"{bundle_name}.zip"

    info(f"Generating support bundle: {bundle_name}")

    secrets = load_secrets(instance)
    secret_values = secrets.values()

    with _private_zip_file(zip_path) as zf:
        # 1. Redacted config
        redacted = _redact(cfg.model_dump())
        config_json = _redact_known_secrets(
            json.dumps(redacted, indent=2), secret_values
        )
        zf.writestr(f"{bundle_name}/config.json", config_json)
        info("  Config (redacted)")

        # 2. Secrets summary (names only, no values)
        secret_summary = {k: f"***({len(v)} chars)" for k, v in secrets.items()}
        zf.writestr(f"{bundle_name}/secrets-summary.json", json.dumps(secret_summary, indent=2))
        info("  Secrets summary")

        # 3. Redacted Compose file
        if instance.compose_path.exists():
            try:
                compose = _render_redacted_compose(instance.compose_path, secrets)
            except (OSError, ValueError, yaml.YAMLError):
                zf.writestr(
                    f"{bundle_name}/compose-omitted.txt",
                    "Compose file omitted because it could not be safely redacted.\n",
                )
                info("  Compose file omitted (could not safely redact it)")
            else:
                zf.writestr(f"{bundle_name}/compose.yml", compose)
                info("  Compose file (environment redacted)")

        # 4. Certificate info
        cert_info = get_cert_info(instance)
        if cert_info:
            cert_json = _redact_known_secrets(
                json.dumps(cert_info, indent=2), secret_values
            )
            zf.writestr(f"{bundle_name}/cert-info.json", cert_json)
            info("  Certificate info")

        # 5. Container status
        if runtime:
            try:
                ps = runtime.compose(
                    ["ps", "--format", "json"], instance, project_name=cfg.stack_name,
                    capture_output=True, text=True, check=False, timeout=10,
                )
                status = ps.stdout or "(no output)"
            except (FileNotFoundError, subprocess.TimeoutExpired):
                status = "(container runtime not available)"
        else:
            status = f"(container runtime not available: {runtime_error})"
        zf.writestr(
            f"{bundle_name}/container-status.txt",
            _redact_known_secrets(status, secret_values),
        )
        info("  Container status")

        # 6. Container logs (last 50 lines each)
        for svc in ["mongo", "opal", "nginx", "rock"]:
            container = f"{cfg.stack_name}-{svc}"
            if not runtime:
                break
            try:
                logs = runtime.run(
                    ["logs", "--tail", "50", container],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                combined = (logs.stdout or "") + (logs.stderr or "")
                if combined.strip():
                    zf.writestr(
                        f"{bundle_name}/logs-{svc}.txt",
                        _redact_known_secrets(combined, secret_values),
                    )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        info("  Container logs (known secrets redacted)")

        # 7. System info
        sys_info = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "container_runtime": runtime.name if runtime else "unavailable",
        }
        if runtime:
            sys_info["compose_command"] = " ".join(runtime.compose_command)
            if runtime.env.get("PODMAN_COMPOSE_PROVIDER"):
                sys_info["compose_provider"] = runtime.env["PODMAN_COMPOSE_PROVIDER"]
            try:
                version = runtime.run(["--version"], capture_output=True, text=True, check=False)
                sys_info["runtime_version"] = version.stdout.strip()
            except FileNotFoundError:
                pass
        elif runtime_error:
            sys_info["runtime_error"] = runtime_error
        system_json = _redact_known_secrets(
            json.dumps(sys_info, indent=2), secret_values
        )
        zf.writestr(f"{bundle_name}/system-info.json", system_json)
        info("  System info")

    success(f"Bundle created: {zip_path}")
    info(
        "Known secret values are redacted; review the bundle before sharing it."
    )
