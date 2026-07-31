"""View container logs."""

import click

from src.core.config_manager import load_config, config_exists
from src.core.container_runtime import RuntimeSelectionError, get_runtime
from src.utils.console import for_each_instance


@click.command()
@click.argument("service", default="opal")
@click.option("-f", "--follow", is_flag=True, help="Follow log output.")
@click.option("-n", "--tail", "lines", default=50, help="Number of lines to show.")
@click.pass_context
def logs(ctx, service, follow, lines):
    """View logs for a service (opal, mongo, nginx, rock, etc.)."""
    def _logs(instance):
        if not config_exists(instance):
            return
        config = load_config(instance)
        container = f"{config.stack_name}-{service}"
        cmd = ["logs", "--tail", str(lines)]
        if follow:
            cmd.append("-f")
        cmd.append(container)
        try:
            runtime = get_runtime(instance)
            result = runtime.run(cmd, check=False)
            if result.returncode != 0:
                raise click.ClickException(
                    f"Failed to read '{service}' logs with exit code "
                    f"{result.returncode}."
                )
        except RuntimeSelectionError as exc:
            raise click.ClickException(str(exc)) from exc
        except FileNotFoundError as exc:
            raise click.ClickException("Container runtime not available.") from exc
        except KeyboardInterrupt:
            pass

    for_each_instance(ctx, _logs)
