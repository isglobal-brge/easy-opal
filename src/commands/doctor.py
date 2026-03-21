"""Diagnostic of easy-opal itself: permissions, config, Docker, registry health."""

import os
import shutil
import subprocess

import click

from src.models.instance import InstanceContext
from src.core.instance_manager import get_home, sync_registry, get_registry_info
from src.core.config_manager import load_config, config_exists
from src.core.ssl import get_cert_info
from src.core.secrets_manager import load_secrets
from src.utils.console import console


class Check:
    def __init__(self, name: str, status: str, detail: str):
        self.name = name
        self.status = status  # "ok", "warn", "fail"
        self.detail = detail

    @property
    def icon(self) -> str:
        return {"ok": "[green]OK[/green]", "warn": "[yellow]WARN[/yellow]", "fail": "[red]FAIL[/red]"}[self.status]


def _check_docker() -> Check:
    try:
        r = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True, check=False)
        if r.returncode == 0:
            ver = r.stdout.strip().split()[-1] if r.stdout.strip() else "?"
            return Check("Docker Compose", "ok", f"v{ver}")
        return Check("Docker Compose", "fail", "Not available")
    except FileNotFoundError:
        return Check("Docker Compose", "fail", "Docker not installed")


def _check_docker_daemon() -> Check:
    try:
        subprocess.run(["docker", "ps"], capture_output=True, check=True)
        return Check("Docker daemon", "ok", "Running")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Check("Docker daemon", "fail", "Not running")


def _check_home() -> Check:
    home = get_home()
    if home.exists():
        return Check("Home directory", "ok", str(home))
    return Check("Home directory", "warn", f"{home} does not exist (will be created)")


def _check_registry() -> Check:
    registry = sync_registry()
    count = len(registry.get("instances", {}))
    stale = 0
    for name, meta in registry.get("instances", {}).items():
        from pathlib import Path
        if not Path(meta["path"]).exists():
            stale += 1
    if stale > 0:
        return Check("Registry", "warn", f"{count} instances, {stale} stale (auto-cleaned)")
    return Check("Registry", "ok", f"{count} instance(s)")


def _check_instance(instance: InstanceContext) -> list[Check]:
    checks = []

    # Config
    if config_exists(instance):
        try:
            cfg = load_config(instance)
            checks.append(Check("Config", "ok", f"schema v{cfg.schema_version}, stack={cfg.stack_name}"))
        except Exception as e:
            checks.append(Check("Config", "fail", f"Invalid: {e}"))
    else:
        checks.append(Check("Config", "warn", "No config (run setup)"))
        return checks

    # Secrets
    secrets = load_secrets(instance)
    admin_pw = secrets.get("OPAL_ADMIN_PASSWORD") or secrets.get("ARMADILLO_ADMIN_PASSWORD")
    if admin_pw:
        mode = os.stat(instance.secrets_path).st_mode & 0o777 if instance.secrets_path.exists() else None
        if mode == 0o600:
            checks.append(Check("Secrets", "ok", f"{len(secrets)} secrets, permissions 0o600"))
        elif mode is not None:
            checks.append(Check("Secrets", "warn", f"Permissions {oct(mode)} (should be 0o600)"))
        else:
            checks.append(Check("Secrets", "warn", "File missing"))
    else:
        checks.append(Check("Secrets", "fail", "No admin password"))

    # SSL
    if cfg.ssl.strategy.value == "self-signed":
        ci = get_cert_info(instance)
        if ci:
            checks.append(Check("SSL cert", "ok", f"SANs: {', '.join(ci['dns_names'])}"))
            # Check CA
            ca_path = instance.certs_dir / "ca.crt"
            if ca_path.exists():
                checks.append(Check("SSL CA", "ok", "Persistent CA present"))
            else:
                checks.append(Check("SSL CA", "warn", "No CA file"))
            # Check key permissions
            key_path = instance.certs_dir / "opal.key"
            if key_path.exists():
                mode = os.stat(key_path).st_mode & 0o777
                if mode == 0o600:
                    checks.append(Check("Key permissions", "ok", "0o600"))
                else:
                    checks.append(Check("Key permissions", "warn", f"{oct(mode)} (should be 0o600)"))
        else:
            checks.append(Check("SSL cert", "warn", "No cert (run setup or cert regenerate)"))
    elif cfg.ssl.strategy.value == "none":
        checks.append(Check("SSL", "ok", "Disabled (none mode)"))
    else:
        checks.append(Check("SSL", "ok", f"Strategy: {cfg.ssl.strategy.value}"))

    # Compose
    if instance.compose_path.exists():
        checks.append(Check("Compose", "ok", str(instance.compose_path)))
    else:
        checks.append(Check("Compose", "warn", "Not generated (run up)"))

    # Lock
    lock_path = instance.root / ".lock"
    if lock_path.exists():
        checks.append(Check("Lock", "warn", f"Lock file present: {lock_path}"))
    else:
        checks.append(Check("Lock", "ok", "No lock"))

    return checks


@click.command()
@click.pass_context
def doctor(ctx):
    """Check easy-opal installation health."""
    console.print("\n[bold]easy-opal doctor[/bold]\n")

    # Global checks
    global_checks = [
        _check_docker(),
        _check_docker_daemon(),
        _check_home(),
        _check_registry(),
    ]

    console.print("[bold]System[/bold]")
    for c in global_checks:
        console.print(f"  {c.icon}  {c.name}: {c.detail}")

    # Instance checks
    instance = ctx.obj.get("instance")
    if instance:
        console.print(f"\n[bold]Instance: {instance.name}[/bold]")
        inst_checks = _check_instance(instance)
        for c in inst_checks:
            console.print(f"  {c.icon}  {c.name}: {c.detail}")

        all_checks = global_checks + inst_checks
    else:
        all_checks = global_checks

    # Summary
    fails = sum(1 for c in all_checks if c.status == "fail")
    warns = sum(1 for c in all_checks if c.status == "warn")
    oks = sum(1 for c in all_checks if c.status == "ok")

    console.print()
    if fails == 0 and warns == 0:
        console.print("[bold green]All checks passed.[/bold green]")
    elif fails == 0:
        console.print(f"[bold yellow]{warns} warning(s), {oks} ok.[/bold yellow]")
    else:
        console.print(f"[bold red]{fails} issue(s), {warns} warning(s), {oks} ok.[/bold red]")
