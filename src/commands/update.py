"""Self-update via git."""

import subprocess
import shutil

import click
from rich.prompt import Confirm

from src.utils.console import console, success, error, info, warning


def _git(args: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git"] + args, capture_output=True, text=True, check=True
        )
        return True, result.stdout.strip()
    except FileNotFoundError:
        return False, "Git not found."
    except subprocess.CalledProcessError as e:
        return False, e.stderr.strip()


@click.command()
def update():
    """Check for and apply updates from the git repository."""
    ok, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    if not ok or branch != "main":
        warning(f"Updates only available on 'main' branch. Currently on: {branch}")
        return

    info("Fetching updates...")
    ok, _ = _git(["fetch"])
    if not ok:
        error("Failed to fetch from remote.")
        return

    ok, status = _git(["status", "-uno"])
    if not ok:
        error("Failed to check status.")
        return

    if "Your branch is up to date" in status:
        success("Already up to date.")
        return

    if "Your branch is behind" not in status:
        warning("Branch has diverged from remote. Resolve manually.")
        return

    success("Update available!")
    if not Confirm.ask("Apply update?", default=True):
        return

    ok, output = _git(["reset", "--hard", "origin/main"])
    if ok:
        success("Updated successfully.")
        console.print(f"[dim]{output}[/dim]")

        # Update dependencies
        uv = shutil.which("uv")
        if uv:
            info("Updating dependencies...")
            subprocess.run([uv, "sync"], check=False)
    else:
        error(f"Update failed: {output}")
