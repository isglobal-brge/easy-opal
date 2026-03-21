"""Backup and restore: full data dumps with native DB tools."""

import json
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path

import click
from rich.table import Table

from src.models.instance import InstanceContext
from src.models.enums import DatabaseType
from src.core.config_manager import load_config, config_exists
from src.core.docker import get_compose_cmd
from src.utils.console import console, success, error, info, dim, warning


def _backups_dir(ctx: InstanceContext) -> Path:
    d = ctx.root / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_in_container(
    container: str, cmd: list[str], output_path: Path, env: dict[str, str] | None = None
) -> bool:
    """Run a command inside a Docker container and capture stdout to a file."""
    full_cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        full_cmd.extend(["-e", f"{k}={v}"])
    full_cmd.extend([container] + cmd)
    try:
        with open(output_path, "wb") as f:
            result = subprocess.run(full_cmd, stdout=f, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            error(f"  Command failed in {container}: {result.stderr.decode()[:200]}")
            return False
        return True
    except FileNotFoundError:
        error("Docker not found.")
        return False


def _restore_to_container(container: str, cmd: list[str], input_path: Path) -> bool:
    """Pipe a file into a command inside a Docker container."""
    full_cmd = ["docker", "exec", "-i", container] + cmd
    try:
        with open(input_path, "rb") as f:
            result = subprocess.run(full_cmd, stdin=f, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            error(f"  Restore failed in {container}: {result.stderr.decode()[:200]}")
            return False
        return True
    except FileNotFoundError:
        error("Docker not found.")
        return False


@click.group()
def backup():
    """Backup and restore instance data."""
    pass


@backup.command()
@click.option("--output", "-o", type=click.Path(), help="Output file path.")
@click.pass_context
def create(ctx, output):
    """Create a full backup (config + database dumps)."""
    instance: InstanceContext = ctx.obj["instance"]
    if not config_exists(instance):
        error("No configuration found.")
        return

    cfg = load_config(instance)
    from src.core.secrets_manager import load_secrets
    secrets = load_secrets(instance)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{cfg.stack_name}-{timestamp}"
    staging_dir = _backups_dir(instance) / backup_name
    staging_dir.mkdir(parents=True, exist_ok=True)

    info(f"Creating backup: {backup_name}")

    # 1. Save config (no secrets — those are separate)
    (staging_dir / "config.json").write_text(cfg.model_dump_json(indent=2))
    dim("  Config saved")

    # 2. Manifest
    manifest = {
        "name": backup_name,
        "timestamp": timestamp,
        "stack_name": cfg.stack_name,
        "opal_version": cfg.opal_version,
        "services": [],
    }

    # 3. MongoDB dump
    mongo_container = f"{cfg.stack_name}-mongo"
    mongo_dump = staging_dir / "mongo.archive"
    info("  Dumping MongoDB...")
    if _run_in_container(mongo_container, ["mongodump", "--archive"], mongo_dump):
        manifest["services"].append({"type": "mongo", "file": "mongo.archive"})
        size_mb = mongo_dump.stat().st_size / (1024 * 1024)
        dim(f"  MongoDB: {size_mb:.1f} MB")
    else:
        warning("  MongoDB dump failed (container might not be running).")

    # 4. Opal server data (tar from volume)
    opal_container = f"{cfg.stack_name}-opal"
    opal_dump = staging_dir / "opal-srv.tar"
    info("  Backing up Opal server data...")
    opal_ok = subprocess.run(
        ["docker", "cp", f"{opal_container}:/srv", str(staging_dir / "opal-srv")],
        capture_output=True, check=False,
    ).returncode == 0
    if opal_ok:
        import tarfile as _tf
        with _tf.open(opal_dump, "w") as t:
            t.add(staging_dir / "opal-srv", arcname="opal-srv")
        import shutil as _sh
        _sh.rmtree(staging_dir / "opal-srv")
        manifest["services"].append({"type": "opal", "file": "opal-srv.tar"})
        size_mb = opal_dump.stat().st_size / (1024 * 1024)
        dim(f"  Opal data: {size_mb:.1f} MB")
    else:
        warning("  Opal data backup failed (container might not be running).")

    # 5. Additional database dumps
    for db in cfg.databases:
        container = f"{cfg.stack_name}-{db.name}"
        dump_file = staging_dir / f"{db.name}.sql"

        info(f"  Dumping {db.type} ({db.name})...")

        if db.type == DatabaseType.POSTGRES:
            ok = _run_in_container(
                container,
                ["pg_dump", "-U", db.user, db.database],
                dump_file,
            )
        elif db.type in (DatabaseType.MYSQL, DatabaseType.MARIADB):
            pw_key = f"{db.name.upper().replace('-', '_')}_PASSWORD"
            db_pw = secrets.get(pw_key, "")
            ok = _run_in_container(
                container,
                ["mysqldump", "-u", "root", db.database],
                dump_file,
                env={"MYSQL_PWD": db_pw},
            )
        else:
            ok = False

        if ok:
            manifest["services"].append({"type": str(db.type), "name": db.name, "file": f"{db.name}.sql"})
            size_mb = dump_file.stat().st_size / (1024 * 1024)
            dim(f"  {db.name}: {size_mb:.1f} MB")
        else:
            warning(f"  {db.name} dump failed.")

    # 5. Write manifest
    (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # 6. Create tar.gz
    if output:
        tar_path = Path(output)
    else:
        tar_path = _backups_dir(instance) / f"{backup_name}.tar.gz"

    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(staging_dir, arcname=backup_name)

    # Clean staging
    import shutil
    shutil.rmtree(staging_dir)

    success(f"Backup created: {tar_path}")
    dim(f"  Services: {', '.join(s.get('name', s['type']) for s in manifest['services'])}")


@backup.command()
@click.argument("backup_file", type=click.Path(exists=True))
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def restore(ctx, backup_file, yes):
    """Restore from a backup file."""
    instance: InstanceContext = ctx.obj["instance"]
    cfg = load_config(instance)

    # Extract tar
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(backup_file, "r:gz") as tar:
            tar.extractall(tmpdir)

        # Find manifest
        entries = list(Path(tmpdir).iterdir())
        if not entries:
            error("Empty backup file.")
            return
        backup_dir = entries[0]
        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            error("Invalid backup: missing manifest.json")
            return

        manifest = json.loads(manifest_path.read_text())
        info(f"Backup: {manifest['name']}")
        info(f"  Stack: {manifest['stack_name']}, Opal: {manifest['opal_version']}")
        info(f"  Services: {', '.join(s.get('name', s['type']) for s in manifest['services'])}")

        if not yes:
            if not click.confirm("Restore this backup? This will overwrite current data."):
                return

        # Restore each service
        for svc in manifest["services"]:
            if svc["type"] == "opal":
                opal_container = f"{cfg.stack_name}-opal"
                opal_tar = backup_dir / svc["file"]
                info("  Restoring Opal server data...")
                # Extract tar to temp, then docker cp back
                import tempfile as _tf2
                with _tf2.TemporaryDirectory() as opal_tmp:
                    with tarfile.open(opal_tar, "r") as t:
                        t.extractall(opal_tmp)
                    srv_dir = Path(opal_tmp) / "opal-srv"
                    if srv_dir.exists():
                        result = subprocess.run(
                            ["docker", "cp", f"{srv_dir}/.", f"{opal_container}:/srv"],
                            capture_output=True, check=False,
                        )
                        if result.returncode == 0:
                            success("  Opal data restored.")
                        else:
                            error("  Opal data restore failed.")
                    else:
                        error("  Opal backup data not found in archive.")

            elif svc["type"] == "mongo":
                mongo_container = f"{cfg.stack_name}-mongo"
                archive = backup_dir / svc["file"]
                info("  Restoring MongoDB...")
                if _restore_to_container(mongo_container, ["mongorestore", "--archive", "--drop"], archive):
                    success("  MongoDB restored.")
                else:
                    error("  MongoDB restore failed.")

            elif svc["type"] in ("postgres",):
                container = f"{cfg.stack_name}-{svc['name']}"
                sql_file = backup_dir / svc["file"]
                db_cfg = next((d for d in cfg.databases if d.name == svc["name"]), None)
                if db_cfg:
                    info(f"  Restoring {svc['name']}...")
                    if _restore_to_container(
                        container,
                        ["psql", "-U", db_cfg.user, db_cfg.database],
                        sql_file,
                    ):
                        success(f"  {svc['name']} restored.")
                    else:
                        error(f"  {svc['name']} restore failed.")

            elif svc["type"] in ("mysql", "mariadb"):
                container = f"{cfg.stack_name}-{svc['name']}"
                sql_file = backup_dir / svc["file"]
                db_cfg = next((d for d in cfg.databases if d.name == svc["name"]), None)
                if db_cfg:
                    info(f"  Restoring {svc['name']}...")
                    if _restore_to_container(
                        container,
                        ["mysql", "-u", "root", db_cfg.database],
                        sql_file,
                    ):
                        success(f"  {svc['name']} restored.")
                    else:
                        error(f"  {svc['name']} restore failed.")

    success("Restore complete.")


@backup.command(name="list")
@click.pass_context
def list_backups(ctx):
    """List available backups."""
    instance: InstanceContext = ctx.obj["instance"]
    backups_dir = _backups_dir(instance)

    files = sorted(backups_dir.glob("*.tar.gz"), reverse=True)
    if not files:
        dim("No backups found.")
        return

    table = Table(title="Backups")
    table.add_column("File", style="cyan")
    table.add_column("Size", style="bold")
    table.add_column("Date", style="dim")

    for f in files:
        size_mb = f.stat().st_size / (1024 * 1024)
        # Parse date from filename: stack-YYYYMMDD_HHMMSS.tar.gz
        name = f.stem.replace(".tar", "")
        parts = name.rsplit("-", 1)
        date = parts[-1] if len(parts) > 1 else "unknown"
        if len(date) == 15:  # YYYYMMDD_HHMMSS
            date = f"{date[:4]}-{date[4:6]}-{date[6:8]} {date[9:11]}:{date[11:13]}:{date[13:15]}"
        table.add_row(f.name, f"{size_mb:.1f} MB", date)

    console.print(table)
