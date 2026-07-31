"""Runtime-neutral automatic image update command."""

import click

from src.core.auto_update import (
    AutoUpdateError,
    ScheduledUpdateDisabled,
    StackNotRunningError,
    update_instance,
)
from src.core.config_manager import config_exists, load_config
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.core.instance_manager import LOCK_TIMEOUT_SECONDS
from src.utils.console import dim, get_instances, info, success, warning


@click.command(name="auto-update")
@click.option(
    "--cleanup",
    is_flag=True,
    help="Remove superseded image IDs after a healthy update.",
)
@click.option("--scheduled", is_flag=True, hidden=True)
@click.pass_context
def auto_update(ctx, cleanup: bool, scheduled: bool):
    """Pull and safely apply updates to running service images."""
    instances = get_instances(ctx)
    if not instances:
        raise click.ClickException("No instance targeted. Use -i <name>.")

    failures: list[str] = []
    for instance in instances:
        if not config_exists(instance):
            failures.append(f"{instance.name}: no configuration found")
            continue

        config = load_config(instance)
        if scheduled and not config.watchtower.enabled:
            dim(f"[{instance.name}] Automatic updates are disabled; skipping.")
            continue

        if scheduled:
            cleanup = config.watchtower.cleanup
        info(f"[{instance.name}] Checking running service images...")
        try:
            runtime = get_runtime(instance)
            result = update_instance(
                runtime,
                instance,
                config.stack_name,
                cleanup=cleanup,
                lock_timeout_seconds=LOCK_TIMEOUT_SECONDS if scheduled else 0,
                scheduled=scheduled,
            )
        except ScheduledUpdateDisabled:
            dim(f"[{instance.name}] Automatic updates are disabled; skipping.")
            continue
        except StackNotRunningError:
            if scheduled:
                dim(f"[{instance.name}] Stack is stopped; skipping scheduled update.")
                continue
            failures.append(
                f"{instance.name}: no running containers found for "
                f"Compose project '{config.stack_name}'"
            )
            continue
        except (AutoUpdateError, RuntimeSelectionError) as exc:
            failures.append(f"{instance.name}: {exc}")
            continue

        if result.changed_images:
            success(
                f"[{instance.name}] Updated {len(result.changed_images)} image(s) "
                "and passed health checks."
            )
            if result.removed_image_ids:
                dim(
                    f"[{instance.name}] Removed "
                    f"{len(result.removed_image_ids)} superseded image(s)."
                )
            for cleanup_error in result.cleanup_errors:
                warning(
                    f"[{instance.name}] Updated successfully, but an old image "
                    f"could not be removed: {cleanup_error}"
                )
        else:
            success(f"[{instance.name}] Images are already current.")

    if failures:
        rendered = "\n".join(f"  - {failure}" for failure in failures)
        raise click.ClickException(f"Automatic update failed:\n{rendered}")
