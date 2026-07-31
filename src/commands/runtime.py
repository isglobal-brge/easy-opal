"""Inspect and select the host's default container runtime."""

import click
from rich.table import Table

from src.core import instance_manager
from src.core.container_runtime import (
    get_runtime_selection,
    probe_runtime,
    probe_runtimes,
)
from src.utils.console import console, success


@click.group()
def runtime():
    """Inspect or select the Docker/Podman default for new setups."""
    pass


@runtime.command()
def status():
    """Show the saved preference and usable runtime pairs."""
    preference = instance_manager.get_default_runtime()
    choice, explicit = get_runtime_selection()
    console.print(
        f"Default runtime for new setups: [bold]{preference}[/bold]"
    )
    if explicit:
        console.print(
            "Active CLI/environment override: "
            f"[bold]{choice}[/bold]"
        )

    available, failures = probe_runtimes()
    table = Table(title="Container runtimes")
    table.add_column("Runtime", style="cyan bold")
    table.add_column("Status")
    table.add_column("Detail")
    for name in ("docker", "podman"):
        if name in available:
            table.add_row(name.capitalize(), "[green]usable[/green]", "")
        else:
            table.add_row(
                name.capitalize(),
                "[red]unavailable[/red]",
                failures.get(name, "unknown failure"),
            )
    console.print(table)
    console.print(
        "Existing instances keep their own runtime bindings; "
        "use 'easy-opal instance list' to inspect them."
    )


@runtime.command(name="select")
@click.argument(
    "name",
    required=False,
    type=click.Choice(["auto", "docker", "podman"], case_sensitive=False),
)
def select_runtime(name: str | None):
    """Select a persistent default; omit NAME to open the wizard."""
    selected_runtime = None
    if name is None:
        available, failures = probe_runtimes()
        if not available:
            detail = "; ".join(
                f"{runtime}: {failure}"
                for runtime, failure in failures.items()
            )
            console.print(
                "[yellow]No runtime is currently usable; only automatic "
                f"detection can be selected.[/yellow] {detail}"
            )

        choices = [
            runtime for runtime in ("docker", "podman") if runtime in available
        ]
        choices.append("auto")
        current = instance_manager.get_default_runtime()
        default = current if current in choices else "auto"
        name = click.prompt(
            "Default runtime for new setups",
            type=click.Choice(choices, case_sensitive=False),
            default=default,
        )
        selected_runtime = available.get(name)

    if name != "auto" and selected_runtime is None:
        selected_runtime, failure = probe_runtime(name)
        if selected_runtime is None:
            raise click.ClickException(
                f"Container runtime '{name}' is not usable with Compose. "
                f"{failure}"
            )

    instance_manager.set_default_runtime(name)
    label = name.capitalize() if name != "auto" else "automatic detection"
    success(f"Default runtime for new setups set to {label}.")
    console.print("Existing instance bindings were not changed.")
    active_choice, explicit = get_runtime_selection()
    if explicit and active_choice != name:
        console.print(
            "[yellow]The active CLI/environment override remains "
            f"'{active_choice}' and still takes precedence.[/yellow]"
        )
