"""Profile updater: pre-pulls profile images on schedule (no restart).

The new images stay in the local Docker cache. They become active only when
the user runs 'easy-opal restart' (or 'up'), which Docker Compose recreates
with the updated image. This decouples upstream pushes from service downtime.
"""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


def _build_updater_script(config: OpalConfig) -> str:
    interval = config.profile_updater.interval_hours * 3600

    pull_lines = []
    for p in config.profiles:
        full = f"{p.image}:{p.tag}"
        pull_lines.append(
            f'  echo "  Pulling {full}..." && '
            f'docker pull {full} 2>&1 | tail -1 || echo "  Failed: {full}"'
        )
    pulls = "\n".join(pull_lines)

    return f"""#!/bin/sh
set -u
INTERVAL={interval}

do_pull() {{
    echo "[$(date)] Pre-pulling profile images..."
{pulls}
    echo "[$(date)] Pre-pull complete. Restart the stack to apply new images."
}}

# Initial pull on startup
do_pull

# Periodic loop
while true; do
    sleep $INTERVAL
    do_pull
done
"""


class ProfileUpdaterService:
    name = "profile-updater"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.profile_updater.enabled

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        script = _build_updater_script(config)
        script_dir = ctx.data_dir / "profile-updater-script"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / "updater.sh"
        script_path.write_text(script)

        return {
            "profile-updater": {
                "image": "docker:cli",
                "container_name": f"{config.stack_name}-profile-updater",
                "restart": "always",
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    f"{script_path}:/updater.sh:ro",
                ],
                "entrypoint": ["sh", "/updater.sh"],
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
