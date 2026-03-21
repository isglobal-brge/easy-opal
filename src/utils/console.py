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
