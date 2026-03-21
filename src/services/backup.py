"""Automated backup service: periodic full backups via Docker socket."""

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


def _build_backup_script(config: OpalConfig) -> str:
    """Generate the shell script that runs inside the backup container."""
    stack = config.stack_name
    interval = config.backup.interval_hours * 3600
    keep = config.backup.keep

    # Build dump commands based on flavor and configured services
    dump_cmds = []

    if config.flavor == "opal":
        # MongoDB
        dump_cmds.append(
            f'echo "  Dumping MongoDB..." && '
            f'docker exec {stack}-mongo mongodump --archive > "$DIR/mongo.archive" 2>/dev/null && '
            f'echo "  MongoDB: OK" || echo "  MongoDB: FAILED"'
        )

        # Opal /srv data
        dump_cmds.append(
            f'echo "  Copying Opal data..." && '
            f'docker cp {stack}-opal:/srv "$DIR/opal-srv" 2>/dev/null && '
            f'tar cf "$DIR/opal-srv.tar" -C "$DIR" opal-srv 2>/dev/null && '
            f'rm -rf "$DIR/opal-srv" && '
            f'echo "  Opal data: OK" || echo "  Opal data: FAILED"'
        )

        # Additional databases
        for db in config.databases:
            if db.external:
                continue
            name = db.name
            container = f"{stack}-{name}"
            if db.type == "postgres":
                dump_cmds.append(
                    f'echo "  Dumping {name}..." && '
                    f'docker exec {container} pg_dump -U {db.user} {db.database} > "$DIR/{name}.sql" 2>/dev/null && '
                    f'echo "  {name}: OK" || echo "  {name}: FAILED"'
                )
            elif db.type in ("mysql", "mariadb"):
                dump_cmds.append(
                    f'echo "  Dumping {name}..." && '
                    f'docker exec {container} mysqldump -u root {db.database} > "$DIR/{name}.sql" 2>/dev/null && '
                    f'echo "  {name}: OK" || echo "  {name}: FAILED"'
                )

    elif config.flavor == "armadillo":
        # Armadillo uses Parquet files on disk
        dump_cmds.append(
            f'echo "  Copying Armadillo data..." && '
            f'docker cp {stack}-armadillo:/data "$DIR/armadillo-data" 2>/dev/null && '
            f'tar cf "$DIR/armadillo-data.tar" -C "$DIR" armadillo-data 2>/dev/null && '
            f'rm -rf "$DIR/armadillo-data" && '
            f'echo "  Armadillo data: OK" || echo "  Armadillo data: FAILED"'
        )

    dumps = "\n".join(dump_cmds)

    return f"""#!/bin/sh
set -e

BACKUP_DIR="/backups"
INTERVAL={interval}
KEEP={keep}
STACK="{stack}"

do_backup() {{
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    NAME="${{STACK}}-${{TIMESTAMP}}"
    DIR="/tmp/${{NAME}}"
    mkdir -p "$DIR"

    echo "[$(date)] Starting backup: $NAME"

    # Dump all services
{dumps}

    # Create manifest
    cat > "$DIR/manifest.json" << MANIFEST
{{
  "name": "$NAME",
  "timestamp": "$TIMESTAMP",
  "stack_name": "$STACK",
  "type": "automated"
}}
MANIFEST

    # Package
    tar czf "$BACKUP_DIR/${{NAME}}.tar.gz" -C /tmp "$NAME" 2>/dev/null
    rm -rf "$DIR"

    echo "[$(date)] Backup complete: ${{NAME}}.tar.gz"

    # Cleanup old backups
    TOTAL=$(ls -1 "$BACKUP_DIR"/*.tar.gz 2>/dev/null | wc -l)
    if [ "$TOTAL" -gt "$KEEP" ]; then
        REMOVE=$((TOTAL - KEEP))
        ls -1t "$BACKUP_DIR"/*.tar.gz | tail -n "$REMOVE" | xargs rm -f
        echo "[$(date)] Cleaned $REMOVE old backup(s), keeping $KEEP"
    fi
}}

# Check if backup is needed immediately (last backup older than interval)
LATEST=$(ls -1t "$BACKUP_DIR"/*.tar.gz 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "[$(date)] No existing backups, running first backup now"
    do_backup
else
    AGE=$(( $(date +%s) - $(stat -c %Y "$LATEST" 2>/dev/null || stat -f %m "$LATEST" 2>/dev/null) ))
    if [ "$AGE" -ge "$INTERVAL" ]; then
        echo "[$(date)] Last backup is ${{AGE}}s old (interval: ${{INTERVAL}}s), running backup"
        do_backup
    else
        WAIT=$((INTERVAL - AGE))
        echo "[$(date)] Last backup is ${{AGE}}s old, next in ${{WAIT}}s"
    fi
fi

# Loop
while true; do
    sleep $INTERVAL
    do_backup
done
"""


class BackupService:
    name = "backup"

    def is_enabled(self, config: OpalConfig) -> bool:
        return config.backup.enabled

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        # Generate backup script
        script = _build_backup_script(config)
        script_dir = ctx.data_dir / "backup-script"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / "backup.sh"
        script_path.write_text(script)

        return {
            "backup": {
                "image": "docker:cli",
                "container_name": f"{config.stack_name}-backup",
                "restart": "always",
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    f"{ctx.root / 'backups'}:/backups",
                    f"{script_path}:/backup.sh:ro",
                ],
                "entrypoint": ["sh", "/backup.sh"],
            }
        }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        return {}
