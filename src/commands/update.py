"""Self-update: supports both git-clone and uv-tool-install modes."""

import subprocess
import shutil

import click
from rich.prompt import Confirm

from src.utils.console import console, success, error, info, warning


def _is_git_repo() -> bool:
    """Check if we're running from a git repository."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _is_tool_install() -> bool:
    """Check if easy-opal was installed via uv tool install."""
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True, text=True, check=False,
        )
        return "easy-opal" in result.stdout
    except FileNotFoundError:
        return False


def _git_update() -> None:
    """Update via git pull."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        branch = result.stdout.strip()
        if branch != "main":
            warning(f"On branch '{branch}', not 'main'. Update only available on main.")
            return

        info("Fetching updates...")
        subprocess.run(["git", "fetch"], capture_output=True, check=True)

        status = subprocess.run(
            ["git", "status", "-uno"],
            capture_output=True, text=True, check=True,
        )

        if "Your branch is up to date" in status.stdout:
            success("Already up to date.")
            return

        if "Your branch is behind" not in status.stdout:
            warning("Branch has diverged from remote. Resolve manually.")
            return

        success("Update available.")
        if not Confirm.ask("Apply update?", default=True):
            return

        subprocess.run(["git", "reset", "--hard", "origin/main"], check=True)
        success("Code updated.")

        uv = shutil.which("uv")
        if uv:
            info("Syncing dependencies...")
            subprocess.run([uv, "sync"], check=False)

    except subprocess.CalledProcessError as e:
        error(f"Git error: {e}")
    except FileNotFoundError:
        error("Git not found.")


def _tool_update() -> None:
    """Update via uv tool upgrade."""
    info("Upgrading easy-opal via uv tool...")
    try:
        result = subprocess.run(
            ["uv", "tool", "upgrade", "easy-opal"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            success("Updated successfully.")
            if result.stdout.strip():
                console.print(f"[dim]{result.stdout.strip()}[/dim]")
        else:
            error(f"Upgrade failed: {result.stderr.strip()}")
    except FileNotFoundError:
        error("uv not found.")


@click.command()
def update():
    """Update easy-opal to the latest version."""
    if _is_git_repo():
        info("Detected git repository.")
        _git_update()
    elif _is_tool_install():
        info("Detected uv tool installation.")
        _tool_update()
    else:
        error("Cannot determine installation method.")
        info("If installed via git: cd into the repo and run 'easy-opal update'")
        info("If installed via uv: run 'uv tool upgrade easy-opal'")
