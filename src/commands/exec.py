"""Execute commands inside containers."""

import click

from src.models.instance import InstanceContext
from src.core.config_manager import load_config, config_exists
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.utils.console import error, require_single_instance


@click.command(name="exec", context_settings={"ignore_unknown_options": True})
@click.argument("service")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def exec_cmd(ctx, service, command):
    """Execute a command inside a container.

    Examples:

      easy-opal exec opal bash

      easy-opal exec mongo mongosh

      easy-opal exec rock R
    """
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        error("No configuration found.")
        return

    config = load_config(instance)
    container = f"{config.stack_name}-{service}"

    cmd = ["exec", "-it", container] + list(command or ["sh"])

    try:
        runtime = get_runtime(instance)
        result = runtime.run(cmd, check=False)
        if result.returncode != 0:
            raise click.ClickException(
                f"Command failed in '{service}' with exit code {result.returncode}."
            )
    except RuntimeSelectionError as exc:
        raise click.ClickException(str(exc)) from exc
    except FileNotFoundError as exc:
        raise click.ClickException("Container runtime not available.") from exc
    except KeyboardInterrupt:
        pass
