"""Health diagnostics — modular, clean, focused."""

import json
import subprocess

import click
import requests

from src.models.instance import InstanceContext
from src.models.enums import SSLStrategy
from src.core.config_manager import load_config, config_exists
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.core.ssl import get_cert_info
from src.utils.console import console, error, require_single_instance


class DiagnosticResult:
    def __init__(self, name: str, status: str, message: str):
        self.name = name
        self.status = status  # "pass", "fail", "warn"
        self.message = message

    @property
    def icon(self) -> str:
        return {"pass": "[green]PASS[/green]", "fail": "[red]FAIL[/red]", "warn": "[yellow]WARN[/yellow]"}.get(
            self.status, "?"
        )


def _json_records(output: str) -> list[dict]:
    """Accept both JSON arrays and newline-delimited JSON from Compose providers."""
    output = output.strip()
    if not output:
        return []
    try:
        parsed = json.loads(output)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        pass
    records = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _check_compose_file(ctx: InstanceContext) -> DiagnosticResult:
    if ctx.compose_path.exists():
        return DiagnosticResult("Compose file", "pass", f"Found at {ctx.compose_path}")
    return DiagnosticResult("Compose file", "fail", "Not found. Run 'easy-opal up' to generate.")


def _check_containers(ctx: InstanceContext, config) -> DiagnosticResult:
    try:
        runtime = get_runtime(ctx)
        result = runtime.compose(
            ["ps", "--format", "json"], ctx, project_name=config.stack_name,
            capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            return DiagnosticResult("Containers", "fail", "Could not query containers.")

        containers = _json_records(result.stdout)

        if not containers:
            return DiagnosticResult("Containers", "fail", "No containers found. Run 'easy-opal up'.")

        running = sum(1 for c in containers if c.get("State") == "running")
        total = len(containers)
        if running == total:
            return DiagnosticResult("Containers", "pass", f"All {total} containers running.")
        return DiagnosticResult("Containers", "warn", f"{running}/{total} running.")
    except RuntimeSelectionError as exc:
        return DiagnosticResult("Containers", "fail", str(exc))
    except FileNotFoundError:
        return DiagnosticResult("Containers", "fail", "Container runtime not available.")


def _check_ssl(ctx: InstanceContext, config) -> DiagnosticResult:
    if config.ssl.strategy == SSLStrategy.NONE:
        return DiagnosticResult("SSL", "pass", "No SSL (none mode).")

    ci = get_cert_info(ctx)
    if not ci:
        return DiagnosticResult("SSL", "fail", "No certificate found. Run 'easy-opal cert regenerate'.")

    return DiagnosticResult("SSL", "pass", f"Valid until {ci['not_after']}, SANs: {', '.join(ci['dns_names'])}")


def _check_endpoint(config) -> DiagnosticResult:
    if config.ssl.strategy == SSLStrategy.NONE:
        url = f"http://localhost:{config.opal_http_port}/"
    else:
        host = config.hosts[0] if config.hosts else "localhost"
        url = f"https://{host}:{config.opal_external_port}/"

    try:
        resp = requests.get(url, timeout=10, verify=False)
        if resp.status_code < 500:
            return DiagnosticResult("Endpoint", "pass", f"{url} responded with {resp.status_code}")
        return DiagnosticResult("Endpoint", "fail", f"{url} returned {resp.status_code}")
    except requests.ConnectionError:
        return DiagnosticResult("Endpoint", "fail", f"Cannot connect to {url}")
    except Exception as e:
        return DiagnosticResult("Endpoint", "fail", str(e))


def _check_databases(ctx: InstanceContext, config) -> list[DiagnosticResult]:
    """Test database connectivity from the Opal container."""
    results = []
    try:
        runtime = get_runtime(ctx)
    except RuntimeSelectionError as exc:
        return [
            DiagnosticResult(f"DB {db.name}", "fail", f"Could not test ({exc})")
            for db in config.databases
        ]

    for db in config.databases:
        container = f"{config.stack_name}-opal"
        port = {"postgres": 5432, "mysql": 3306, "mariadb": 3306}.get(db.type, 5432)
        try:
            r = runtime.run(
                ["exec", container, "bash", "-c", f"</dev/tcp/{db.name}/{port}"],
                capture_output=True, check=False, timeout=10,
            )
            if r.returncode == 0:
                results.append(DiagnosticResult(f"DB {db.name}", "pass", f"Reachable on port {port}"))
            else:
                results.append(DiagnosticResult(f"DB {db.name}", "fail", f"Cannot reach {db.name}:{port}"))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            results.append(DiagnosticResult(f"DB {db.name}", "fail", "Could not test (container runtime unavailable)"))
    return results


@click.command()
@click.option("--quiet", "-q", is_flag=True, help="Summary only.")
@click.pass_context
def diagnose(ctx, quiet):
    """Run health diagnostics on the stack."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        error("No configuration found. Run 'easy-opal setup' first.")
        return

    config = load_config(instance)
    results: list[DiagnosticResult] = [
        _check_compose_file(instance),
        _check_containers(instance, config),
        _check_ssl(instance, config),
        _check_endpoint(config),
        *_check_databases(instance, config),
    ]

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warn")

    if quiet:
        if failed == 0:
            console.print(f"[green]HEALTHY[/green] — {passed} passed, {warned} warnings")
        else:
            console.print(f"[red]ISSUES[/red] — {failed} failed, {warned} warnings, {passed} passed")
            raise click.exceptions.Exit(1)
        return

    console.print("\n[bold]Health Diagnostic Report[/bold]\n")
    for r in results:
        console.print(f"  {r.icon}  [bold]{r.name}[/bold]: {r.message}")

    console.print()
    if failed == 0:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print(f"[bold red]{failed} check(s) failed.[/bold red] See above for details.")
        raise click.exceptions.Exit(1)
