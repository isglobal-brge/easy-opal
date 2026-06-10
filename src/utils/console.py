import click
from rich.console import Console

console = Console()

HEADER = r"""
[bold green]=========================================================
                                                       _
                                                      | |
  ___   __ _  ___  _   _           ___   _ __    __ _ | |
 / _ \ / _` |/ __|| | | | ______  / _ \ | '_ \  / _` || |
|  __/| (_| |\__ \| |_| ||______|| (_) || |_) || (_| || |
 \___| \__,_||___/ \__, |         \___/ | .__/  \__,_||_|
                    __/ |               | |
                   |___/                |_|
=========================================================[/bold green]
"""


def display_header() -> None:
    console.print(HEADER)
    console.print(
        "Made with [red]♥[/red] by [bold link=https://davidsarratgonzalez.github.io]David Sarrat González[/bold link]"
    )
    console.print(
        "[bold link=https://brge.isglobal.org]Bioinformatic Research Group in Epidemiology (BRGE)[/bold link]"
    )
    console.print(
        "[bold link=https://www.isglobal.org]Barcelona Institute for Global Health (ISGlobal)[/bold link]"
    )
    console.print()


def success(msg: str) -> None:
    console.print(f"[green]{msg}[/green]")


def warning(msg: str) -> None:
    console.print(f"[yellow]{msg}[/yellow]")


def error(msg: str) -> None:
    console.print(f"[bold red]{msg}[/bold red]")


def info(msg: str) -> None:
    console.print(f"[cyan]{msg}[/cyan]")


def dim(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")


def get_instances(ctx) -> list:
    """All targeted instances (--all / -i a,b), else the single one, else []."""
    instances = ctx.obj.get("instances")
    if instances:
        return instances
    single = ctx.obj.get("instance")
    return [single] if single is not None else []


def require_single_instance(ctx):
    """Return the single targeted instance; error if zero or many were targeted.

    Used by commands that act on exactly one instance, so passing --all or
    -i a,b fails loudly instead of silently operating on just the first.
    """
    instances = ctx.obj.get("instances")
    if instances and len(instances) > 1:
        names = ", ".join(i.name for i in instances)
        raise click.ClickException(
            f"This command targets a single instance, but several were given "
            f"({names}). Re-run with -i <name> to choose one."
        )
    single = ctx.obj.get("instance")
    if single is None:
        raise click.ClickException("No instance targeted. Use -i <name>.")
    return single


def for_each_instance(ctx, fn):
    """Run fn(instance) for --all or -i a,b,c. Falls back to the single instance.

    Hardened to never assume ctx.obj['instance'] exists.
    """
    instances = get_instances(ctx)
    for inst in instances:
        if len(instances) > 1:
            console.print(f"\n[bold cyan]--- {inst.name} ---[/bold cyan]")
        fn(inst)
