"""Application and database backup/restore with native DB tools."""

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.table import Table

from src.core.config_manager import load_config, config_exists
from src.core.container_runtime import get_runtime
from src.core.instance_manager import InstanceLock, LOCK_TIMEOUT_SECONDS
from src.models.config import DatabaseConfig, OpalConfig
from src.models.enums import DatabaseType
from src.models.instance import InstanceContext
from src.utils.console import (
    console,
    dim,
    error,
    info,
    require_single_instance,
    success,
    warning,
)


def _backups_dir(ctx: InstanceContext) -> Path:
    d = ctx.root / "backups"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    d.chmod(0o700)
    return d


def _run_in_container(
    runtime, container: str, cmd: list[str], output_path: Path
) -> bool:
    """Run a command inside a container and capture stdout to a file."""
    full_cmd = ["exec", container, *cmd]
    try:
        with open(output_path, "wb") as f:
            result = runtime.run(
                full_cmd, stdout=f, stderr=subprocess.PIPE, check=False
            )
        if result.returncode != 0:
            error(
                f"  Command failed in {container} "
                f"(exit code {result.returncode})."
            )
            return False
        return True
    except FileNotFoundError:
        error("Container runtime not found.")
        return False


def _restore_to_container(
    runtime,
    container: str,
    cmd: list[str],
    input_path: Path,
) -> bool:
    """Pipe a file into a command inside a container."""
    full_cmd = ["exec", "-i", container, *cmd]
    try:
        with open(input_path, "rb") as f:
            result = runtime.run(full_cmd, stdin=f, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            error(
                f"  Restore failed in {container} "
                f"(exit code {result.returncode})."
            )
            return False
        return True
    except FileNotFoundError:
        error("Container runtime not found.")
        return False


_PROJECT_LABELS = (
    "com.docker.compose.project",
    "io.podman.compose.project",
)
_MANAGED_BACKUP_TIMESTAMP_RE = re.compile(
    r"(?:\d{8}_\d{6}|\d{8}T\d{6}_\d{6}Z)"
)
_MYSQL_ROOT_EXEC_SCRIPT = """\
set -eu
MYSQL_PWD=${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is not set}
export MYSQL_PWD
exec "$@"
"""
_MARIADB_ROOT_EXEC_SCRIPT = """\
set -eu
MYSQL_PWD=${MARIADB_ROOT_PASSWORD:?MARIADB_ROOT_PASSWORD is not set}
export MYSQL_PWD
exec "$@"
"""


def _stack_is_running(runtime, project_name: str) -> bool:
    """Check running engine containers carrying either Compose project label."""
    container_ids: set[str] = set()
    for project_label in _PROJECT_LABELS:
        try:
            result = runtime.run(
                [
                    "ps",
                    "--filter",
                    f"label={project_label}={project_name}",
                    "--format",
                    "{{.ID}}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise click.ClickException(
                f"Could not inspect the stack before scheduled backup: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = result.stderr or result.stdout or ""
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            suffix = f": {detail.strip()}" if detail.strip() else ""
            raise click.ClickException(
                f"Could not inspect the stack before scheduled backup{suffix}"
            )
        output = result.stdout
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        container_ids.update(
            line.strip() for line in output.splitlines() if line.strip()
        )
    return bool(container_ids)


def _mysql_family_command(
    database_type: DatabaseType, database: str, *, restore: bool
) -> list[str]:
    """Build a root SQL command that reads its password inside the container."""
    if database_type == DatabaseType.MYSQL:
        script = _MYSQL_ROOT_EXEC_SCRIPT
        executable = "mysql" if restore else "mysqldump"
    elif database_type == DatabaseType.MARIADB:
        script = _MARIADB_ROOT_EXEC_SCRIPT
        executable = "mariadb" if restore else "mariadb-dump"
    else:
        raise ValueError(f"Unsupported MySQL-family database: {database_type}")

    options = ["-u", "root", database]
    if not restore:
        options.insert(0, "--single-transaction")
    return [
        "sh",
        "-c",
        script,
        "easy-opal-database",
        executable,
        *options,
    ]


def _private_staging_dir(parent: Path, backup_name: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=f".{backup_name}-", dir=parent))
    path.chmod(0o700)
    return path


def _managed_backups(directory: Path, stack_name: str) -> list[Path]:
    prefix = f"{stack_name}-"
    managed = []
    for archive in directory.glob("*.tar.gz"):
        name = archive.name.removesuffix(".tar.gz")
        if name.startswith(prefix) and _MANAGED_BACKUP_TIMESTAMP_RE.fullmatch(
            name[len(prefix) :]
        ):
            managed.append(archive)
    return sorted(
        managed, key=lambda archive: archive.stat().st_mtime_ns, reverse=True
    )


def _publish_archive(staging_dir: Path, backup_name: str, destination: Path) -> None:
    """Atomically publish a private archive without clobbering an old one on failure."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        with tarfile.open(temporary_path, "w:gz") as tar:
            tar.add(staging_dir, arcname=backup_name)
        temporary_path.chmod(0o600)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _archive_container_directory(
    runtime,
    container: str,
    source: str,
    staging_dir: Path,
    directory_name: str,
    archive_name: str,
) -> Path | None:
    """Copy a container directory and package it as an uncompressed tar."""
    directory = staging_dir / directory_name
    copied = runtime.run(
        ["cp", f"{container}:{source}", str(directory)],
        capture_output=True,
        check=False,
    )
    if copied.returncode != 0:
        return None

    archive = staging_dir / archive_name
    with tarfile.open(archive, "w") as tar:
        tar.add(directory, arcname=directory_name)
    shutil.rmtree(directory)
    return archive


_RESTORE_SERVICE_TYPES = frozenset(
    {"opal", "armadillo", "mongo", "postgres", "mysql", "mariadb"}
)
_DATABASE_SERVICE_TYPES = frozenset({"postgres", "mysql", "mariadb"})
_FLAVOR_SERVICE_TYPES = {
    "opal": frozenset({"opal", "mongo"}),
    "armadillo": frozenset({"armadillo"}),
}
_MAX_RESTORE_ARCHIVE_MEMBERS = 200_000
_MAX_RESTORE_ARCHIVE_BYTES = 50 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class _RestoreItem:
    service_type: str
    label: str
    source_file: Path
    container: str
    database: DatabaseConfig | None = None
    prepared_directory: Path | None = None
    target_directory: str | None = None


def _validated_archive_members(
    archive: tarfile.TarFile, label: str
) -> list[tarfile.TarInfo]:
    """Fail closed on excessive declared archive resources before extraction."""
    members = []
    declared_size = 0
    for member in archive:
        members.append(member)
        if len(members) > _MAX_RESTORE_ARCHIVE_MEMBERS:
            raise ValueError(
                f"{label} has too many members "
                f"({len(members)} > {_MAX_RESTORE_ARCHIVE_MEMBERS})"
            )
        if member.size < 0:
            raise ValueError(f"{label} contains a member with a negative size")
        declared_size += member.size
        if declared_size > _MAX_RESTORE_ARCHIVE_BYTES:
            raise ValueError(
                f"{label} declares more than {_MAX_RESTORE_ARCHIVE_BYTES} bytes"
            )
    return members


def _validate_manifest_services(value) -> list[dict]:
    """Validate the restore-relevant portion of a backup manifest."""
    if not isinstance(value, list) or not value:
        raise ValueError("'services' must be a non-empty list of objects")

    seen_services: set[tuple[str, str | None]] = set()
    seen_files: set[str] = set()
    for index, service in enumerate(value):
        if not isinstance(service, dict):
            raise ValueError(f"service {index} must be an object")

        service_type = service.get("type")
        if service_type not in _RESTORE_SERVICE_TYPES:
            raise ValueError(f"service {index} has an unsupported 'type'")

        file_name = service.get("file")
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError(f"service {index} must have a non-empty 'file'")

        name = service.get("name")
        if service_type in _DATABASE_SERVICE_TYPES and (
            not isinstance(name, str) or not name.strip()
        ):
            raise ValueError(
                f"database service {index} must have a non-empty 'name'"
            )
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError(f"service {index} has an invalid 'name'")

        service_key = (
            ("database", name)
            if service_type in _DATABASE_SERVICE_TYPES
            else (service_type, None)
        )
        if service_key in seen_services:
            target = (
                name if service_type in _DATABASE_SERVICE_TYPES else service_type
            )
            raise ValueError(f"service {index} duplicates restore target '{target}'")
        seen_services.add(service_key)

        if file_name in seen_files:
            raise ValueError(f"service {index} reuses data file '{file_name}'")
        seen_files.add(file_name)

    return value


def _prepare_restore_plan(
    config: OpalConfig,
    flavor: str,
    services: list[dict],
    backup_dir: Path,
    prepared_root: Path,
) -> list[_RestoreItem]:
    """Resolve and validate every restore target before touching live data."""
    if flavor not in _FLAVOR_SERVICE_TYPES:
        raise ValueError(f"unsupported backup flavor {flavor!r}")
    if flavor != config.flavor:
        raise ValueError(
            f"backup flavor '{flavor}' is incompatible with this "
            f"'{config.flavor}' instance"
        )

    backup_root = backup_dir.resolve()
    seen_containers: dict[str, int] = {}
    seen_source_files: dict[Path, int] = {}
    plan: list[_RestoreItem] = []

    for index, service in enumerate(services):
        service_type = service["type"]
        if (
            service_type not in _DATABASE_SERVICE_TYPES
            and service_type not in _FLAVOR_SERVICE_TYPES[flavor]
        ):
            raise ValueError(
                f"service {index} type '{service_type}' is incompatible with "
                f"backup flavor '{flavor}'"
            )

        source_file = (backup_dir / service["file"]).resolve()
        try:
            source_file.relative_to(backup_root)
        except ValueError as exc:
            raise ValueError(
                f"service {index} references data outside the backup"
            ) from exc
        if not source_file.is_file():
            raise ValueError(f"missing data file for service {index}")
        if source_file.stat().st_size == 0:
            raise ValueError(f"data file for service {index} is empty")
        previous_source_index = seen_source_files.get(source_file)
        if previous_source_index is not None:
            raise ValueError(
                f"services {previous_source_index} and {index} reference the "
                "same data file"
            )
        seen_source_files[source_file] = index

        database = None
        prepared_directory = None
        target_directory = None

        if service_type in _DATABASE_SERVICE_TYPES:
            matches = [
                database
                for database in config.databases
                if database.name == service["name"]
            ]
            if not matches:
                raise ValueError(
                    f"database '{service['name']}' is not configured"
                )
            if len(matches) != 1:
                raise ValueError(
                    f"database '{service['name']}' is configured more than once"
                )
            database = matches[0]
            if database.external:
                raise ValueError(
                    f"database '{service['name']}' is external and cannot be restored"
                )
            if str(database.type) != service_type:
                raise ValueError(
                    f"database '{service['name']}' is configured as "
                    f"'{database.type}', not '{service_type}'"
                )
            container = f"{config.stack_name}-{database.name}"
            label = database.name
        elif service_type in ("opal", "armadillo"):
            container = f"{config.stack_name}-{service_type}"
            directory_name = (
                "armadillo-data" if service_type == "armadillo" else "opal-srv"
            )
            target_directory = (
                "/data" if service_type == "armadillo" else "/srv"
            )
            label = (
                "Armadillo data" if service_type == "armadillo" else "Opal data"
            )
            application_root = prepared_root / str(index)
            application_root.mkdir(mode=0o700)
            try:
                with tarfile.open(source_file, "r") as archive:
                    members = _validated_archive_members(archive, f"{label} archive")
                    archive.extractall(
                        application_root,
                        members=members,
                        filter="data",
                    )
            except (OSError, tarfile.TarError, ValueError) as exc:
                raise ValueError(f"invalid {label} archive: {exc}") from exc
            prepared_directory = application_root / directory_name
            if (
                not prepared_directory.is_dir()
                or prepared_directory.is_symlink()
            ):
                raise ValueError(f"{label} directory is missing from its archive")
        else:
            container = f"{config.stack_name}-mongo"
            label = "MongoDB"

        previous_index = seen_containers.get(container)
        if previous_index is not None:
            raise ValueError(
                f"services {previous_index} and {index} both target container "
                f"'{container}'"
            )
        seen_containers[container] = index
        plan.append(
            _RestoreItem(
                service_type=service_type,
                label=label,
                source_file=source_file,
                container=container,
                database=database,
                prepared_directory=prepared_directory,
                target_directory=target_directory,
            )
        )

    return plan


_TOOLS_PREFLIGHT_SCRIPT = """\
set -eu
for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || exit 20
done
"""
_APPLICATION_PREFLIGHT_SCRIPT = """\
set -eu
target=$1
shift
[ -d "$target" ] && [ -w "$target" ] || exit 22
for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || exit 20
done
"""
_MYSQL_RESTORE_PREFLIGHT_SCRIPT = """\
set -eu
command -v "$1" >/dev/null 2>&1 || exit 20
test -n "${MYSQL_ROOT_PASSWORD:-}" || exit 21
"""
_MARIADB_RESTORE_PREFLIGHT_SCRIPT = """\
set -eu
command -v "$1" >/dev/null 2>&1 || exit 20
test -n "${MARIADB_ROOT_PASSWORD:-}" || exit 21
"""


def _command_detail(result: subprocess.CompletedProcess) -> str:
    detail = result.stderr or result.stdout or ""
    if isinstance(detail, bytes):
        detail = detail.decode(errors="replace")
    return detail.strip()


def _preflight_restore_targets(runtime, plan: list[_RestoreItem]) -> None:
    """Verify all target containers and required tools without changing data."""
    for item in plan:
        try:
            result = runtime.run(
                [
                    "inspect",
                    "--format",
                    "{{.State.Status}}",
                    item.container,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise click.ClickException(
                f"Restore preflight could not inspect container "
                f"'{item.container}': {exc}"
            ) from exc
        if result.returncode != 0:
            detail = _command_detail(result)
            suffix = f": {detail}" if detail else ""
            raise click.ClickException(
                f"Restore preflight could not find container "
                f"'{item.container}'{suffix}"
            )
        state = result.stdout or ""
        if isinstance(state, bytes):
            state = state.decode(errors="replace")
        if state.strip() != "running":
            raise click.ClickException(
                f"Restore preflight requires container '{item.container}' to be "
                f"running (current state: {state.strip() or 'unknown'})."
            )

    for item in plan:
        if item.service_type in ("opal", "armadillo"):
            assert item.target_directory is not None
            command = [
                "exec",
                item.container,
                "sh",
                "-c",
                _APPLICATION_PREFLIGHT_SCRIPT,
                "easy-opal-restore-preflight",
                item.target_directory,
                "mkdir",
                "mv",
                "rm",
                "stat",
                "chown",
            ]
        elif item.service_type == "mongo":
            command = [
                "exec",
                item.container,
                "sh",
                "-c",
                _TOOLS_PREFLIGHT_SCRIPT,
                "easy-opal-restore-preflight",
                "mongorestore",
            ]
        elif item.service_type == "postgres":
            command = [
                "exec",
                item.container,
                "sh",
                "-c",
                _TOOLS_PREFLIGHT_SCRIPT,
                "easy-opal-restore-preflight",
                "psql",
            ]
        else:
            client = "mysql" if item.service_type == "mysql" else "mariadb"
            script = (
                _MYSQL_RESTORE_PREFLIGHT_SCRIPT
                if item.service_type == "mysql"
                else _MARIADB_RESTORE_PREFLIGHT_SCRIPT
            )
            command = [
                "exec",
                item.container,
                "sh",
                "-c",
                script,
                "easy-opal-restore-preflight",
                client,
            ]

        try:
            result = runtime.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise click.ClickException(
                f"Restore preflight failed in container '{item.container}': {exc}"
            ) from exc
        if result.returncode != 0:
            if result.returncode == 20:
                reason = "a required restore tool is unavailable"
            elif result.returncode == 21:
                reason = "the required root password environment is unavailable"
            elif result.returncode == 22:
                reason = "the target data directory is unavailable or not writable"
            else:
                detail = _command_detail(result)
                reason = detail or f"probe exited with code {result.returncode}"
            raise click.ClickException(
                f"Restore preflight failed in container '{item.container}': {reason}."
            )


_REPLACE_DIRECTORY_SCRIPT = r"""
set -eu
target=$1
stage=$2
previous=$3
phase=preserve

move_contents() {
    source=$1
    destination=$2
    move_failed=0
    for item in "$source"/* "$source"/.[!.]* "$source"/..?*; do
        if [ -e "$item" ] || [ -L "$item" ]; then
            mv "$item" "$destination"/ || move_failed=1
        fi
    done
    return "$move_failed"
}

rollback() {
    status=$?
    trap - EXIT HUP INT TERM
    set +e
    rollback_failed=0
    if [ "$phase" = install ]; then
        for item in "$target"/* "$target"/.[!.]* "$target"/..?*; do
            [ "$item" = "$stage" ] && continue
            [ "$item" = "$previous" ] && continue
            if [ -e "$item" ] || [ -L "$item" ]; then
                rm -rf "$item" || rollback_failed=1
            fi
        done
    fi
    if [ -d "$previous" ]; then
        move_contents "$previous" "$target" || rollback_failed=1
    fi
    if [ "$rollback_failed" -ne 0 ]; then
        printf '%s\n' "Automatic rollback was incomplete; recovery data was preserved." >&2
        exit 70
    fi
    rm -rf "$stage" "$previous"
    exit "$status"
}
trap rollback EXIT HUP INT TERM

mkdir "$previous"
for item in "$target"/* "$target"/.[!.]* "$target"/..?*; do
    [ "$item" = "$stage" ] && continue
    [ "$item" = "$previous" ] && continue
    if [ -e "$item" ] || [ -L "$item" ]; then
        mv "$item" "$previous"/
    fi
done

phase=install
move_contents "$stage" "$target"
trap - EXIT HUP INT TERM
rm -rf "$stage" "$previous"
"""


def _replace_container_directory(
    runtime, container: str, source: Path, target: str
) -> bool:
    """Replace a container directory after staging all new data safely."""
    token = uuid.uuid4().hex
    stage = f"{target}/.easy-opal-restore-stage-{token}"
    previous = f"{target}/.easy-opal-restore-previous-{token}"

    owner_result = runtime.run(
        ["exec", container, "stat", "-c", "%u:%g", target],
        capture_output=True,
        text=True,
        check=False,
    )
    owner = owner_result.stdout or ""
    if isinstance(owner, bytes):
        owner = owner.decode(errors="replace")
    owner = owner.strip()
    if owner_result.returncode != 0 or not re.fullmatch(r"[0-9]+:[0-9]+", owner):
        return False

    prepared = runtime.run(
        ["exec", "--user", "0", container, "mkdir", "-p", stage],
        capture_output=True,
        check=False,
    )
    if prepared.returncode != 0:
        return False

    copied = runtime.run(
        ["cp", f"{source}/.", f"{container}:{stage}"],
        capture_output=True,
        check=False,
    )
    if copied.returncode != 0:
        runtime.run(
            ["exec", "--user", "0", container, "rm", "-rf", stage],
            capture_output=True,
            check=False,
        )
        return False

    owned = runtime.run(
        ["exec", "--user", "0", container, "chown", "-R", owner, stage],
        capture_output=True,
        check=False,
    )
    if owned.returncode != 0:
        runtime.run(
            ["exec", "--user", "0", container, "rm", "-rf", stage],
            capture_output=True,
            check=False,
        )
        return False

    replaced = runtime.run(
        [
            "exec",
            "--user",
            "0",
            container,
            "sh",
            "-c",
            _REPLACE_DIRECTORY_SCRIPT,
            "easy-opal-restore",
            target,
            stage,
            previous,
        ],
        capture_output=True,
        check=False,
    )
    if replaced.returncode == 70:
        raise click.ClickException(
            f"Restore of '{container}:{target}' failed and automatic rollback "
            "was incomplete. Stop writes and recover the preserved original data "
            f"from '{container}:{previous}'. Staged replacement data remains at "
            f"'{container}:{stage}'."
        )
    return replaced.returncode == 0


def _create_staged_payload(
    runtime,
    cfg,
    staging_dir: Path,
    backup_name: str,
    timestamp: str,
) -> dict:
    """Create all backup members inside a private staging directory."""
    config_path = staging_dir / "config.json"
    config_path.write_text(cfg.model_dump_json(indent=2))
    config_path.chmod(0o600)
    dim("  Config saved")

    manifest = {
        "name": backup_name,
        "timestamp": timestamp,
        "stack_name": cfg.stack_name,
        "flavor": cfg.flavor,
        "opal_version": cfg.opal_version,
        "application_version": (
            cfg.armadillo.version if cfg.flavor == "armadillo" else cfg.opal_version
        ),
        "services": [],
    }
    failures: list[str] = []

    if cfg.flavor == "opal":
        mongo_container = f"{cfg.stack_name}-mongo"
        mongo_dump = staging_dir / "mongo.archive"
        info("  Dumping MongoDB...")
        if _run_in_container(
            runtime, mongo_container, ["mongodump", "--archive"], mongo_dump
        ):
            manifest["services"].append(
                {"type": "mongo", "file": "mongo.archive"}
            )
            size_mb = mongo_dump.stat().st_size / (1024 * 1024)
            dim(f"  MongoDB: {size_mb:.1f} MB")
        else:
            warning("  MongoDB dump failed (container might not be running).")
            failures.append("MongoDB")

    if cfg.flavor == "armadillo":
        service_type = "armadillo"
        container = f"{cfg.stack_name}-armadillo"
        source = "/data"
        directory_name = "armadillo-data"
        archive_name = "armadillo-data.tar"
        label = "Armadillo data"
    else:
        service_type = "opal"
        container = f"{cfg.stack_name}-opal"
        source = "/srv"
        directory_name = "opal-srv"
        archive_name = "opal-srv.tar"
        label = "Opal data"

    info(f"  Backing up {label}...")
    application_dump = _archive_container_directory(
        runtime,
        container,
        source,
        staging_dir,
        directory_name,
        archive_name,
    )
    if application_dump:
        manifest["services"].append(
            {"type": service_type, "file": archive_name}
        )
        size_mb = application_dump.stat().st_size / (1024 * 1024)
        dim(f"  {label}: {size_mb:.1f} MB")
    else:
        warning(f"  {label} backup failed (container might not be running).")
        failures.append(label)

    for db in cfg.databases:
        if db.external:
            dim(f"  Skipping external database {db.name}.")
            continue
        container = f"{cfg.stack_name}-{db.name}"
        dump_file = staging_dir / f"{db.name}.sql"

        info(f"  Dumping {db.type} ({db.name})...")

        if db.type == DatabaseType.POSTGRES:
            ok = _run_in_container(
                runtime,
                container,
                [
                    "pg_dump",
                    "--clean",
                    "--if-exists",
                    "-U",
                    db.user,
                    db.database,
                ],
                dump_file,
            )
        elif db.type in (DatabaseType.MYSQL, DatabaseType.MARIADB):
            ok = _run_in_container(
                runtime,
                container,
                _mysql_family_command(db.type, db.database, restore=False),
                dump_file,
            )
        else:
            ok = False

        if ok:
            manifest["services"].append(
                {
                    "type": str(db.type),
                    "name": db.name,
                    "file": f"{db.name}.sql",
                }
            )
            size_mb = dump_file.stat().st_size / (1024 * 1024)
            dim(f"  {db.name}: {size_mb:.1f} MB")
        else:
            warning(f"  {db.name} dump failed.")
            failures.append(db.name)

    if failures:
        raise click.ClickException(
            "Backup failed for "
            + ", ".join(failures)
            + "; no archive was created."
        )

    manifest_path = staging_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    manifest_path.chmod(0o600)
    return manifest


@click.group()
def backup():
    """Backup and restore instance data."""
    pass


@backup.command()
@click.option("--output", "-o", type=click.Path(), help="Output file path.")
@click.option("--scheduled", is_flag=True, hidden=True)
@click.pass_context
def create(ctx, output, scheduled):
    """Create an application/database backup."""
    instance: InstanceContext = require_single_instance(ctx)
    if not config_exists(instance):
        raise click.ClickException("No configuration found.")

    cfg = load_config(instance)
    if scheduled and not cfg.backup.enabled:
        dim("Automated backups are disabled; skipping.")
        return

    try:
        lock = (
            InstanceLock(instance, timeout_seconds=LOCK_TIMEOUT_SECONDS)
            if scheduled
            else InstanceLock(instance)
        )
        ctx.with_resource(lock)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    cfg = load_config(instance)
    if scheduled and not cfg.backup.enabled:
        dim("Automated backups were disabled while waiting; skipping.")
        return

    runtime = get_runtime(instance)
    if scheduled and not _stack_is_running(runtime, cfg.stack_name):
        dim("Stack is stopped; skipping scheduled backup.")
        return

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    backup_name = f"{cfg.stack_name}-{timestamp}"
    backups_dir = _backups_dir(instance)
    staging_dir = _private_staging_dir(backups_dir, backup_name)
    info(f"Creating backup: {backup_name}")

    tar_path = (
        Path(output).expanduser()
        if output
        else backups_dir / f"{backup_name}.tar.gz"
    )
    try:
        manifest = _create_staged_payload(
            runtime, cfg, staging_dir, backup_name, timestamp
        )
        _publish_archive(staging_dir, backup_name, tar_path)
    except (OSError, tarfile.TarError) as exc:
        raise click.ClickException(f"Could not create backup archive: {exc}") from exc
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    success(f"Backup created: {tar_path}")
    services = ", ".join(
        service.get("name", service["type"])
        for service in manifest["services"]
    )
    dim(f"  Services: {services}")

    if scheduled and cfg.backup.keep > 0:
        backups = _managed_backups(_backups_dir(instance), cfg.stack_name)
        for expired in backups[cfg.backup.keep :]:
            expired.unlink()
        removed = max(0, len(backups) - cfg.backup.keep)
        if removed:
            dim(f"  Removed {removed} expired backup(s).")


@backup.command()
@click.argument("backup_file", type=click.Path(exists=True))
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def restore(ctx, backup_file, yes):
    """Restore from a backup file."""
    instance: InstanceContext = require_single_instance(ctx)
    try:
        ctx.with_resource(InstanceLock(instance))
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    cfg = load_config(instance)

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with tarfile.open(backup_file, "r:gz") as tar:
                members = _validated_archive_members(tar, "backup archive")
                tar.extractall(tmpdir, members=members, filter="data")
        except (OSError, tarfile.TarError, ValueError) as exc:
            raise click.ClickException(f"Invalid backup archive: {exc}") from exc

        entries = list(Path(tmpdir).iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise click.ClickException(
                "Invalid backup: expected one top-level directory."
            )
        backup_dir = entries[0]
        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            raise click.ClickException("Invalid backup: missing manifest.json")

        try:
            manifest = json.loads(manifest_path.read_text())
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
            services = _validate_manifest_services(manifest["services"])
            flavor = manifest.get("flavor", "opal")
            prepared_root = Path(tmpdir) / ".restore-preflight"
            prepared_root.mkdir(mode=0o700)
            plan = _prepare_restore_plan(
                cfg,
                flavor,
                services,
                backup_dir,
                prepared_root,
            )
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise click.ClickException(f"Invalid backup manifest: {exc}") from exc

        version = manifest.get("application_version") or manifest.get(
            "opal_version", "unknown"
        )
        info(f"Backup: {manifest.get('name', Path(backup_file).name)}")
        info(
            f"  Stack: {manifest.get('stack_name', 'unknown')}, "
            f"{flavor}: {version}"
        )
        service_names = ", ".join(
            service.get("name") or service.get("type", "?")
            for service in services
        )
        info(f"  Services: {service_names}")

        if not yes:
            if not click.confirm(
                "Restore this backup? This will overwrite current data."
            ):
                return

        # Run readiness checks immediately before the first mutation. This
        # narrows the window in which a target could change after preflight and
        # avoids touching the container runtime when the operator cancels.
        runtime = get_runtime(instance)
        _preflight_restore_targets(runtime, plan)

        failures: list[str] = []

        for item in plan:
            if item.service_type in ("opal", "armadillo"):
                assert item.prepared_directory is not None
                assert item.target_directory is not None
                info(f"  Restoring {item.label}...")
                if _replace_container_directory(
                    runtime,
                    item.container,
                    item.prepared_directory,
                    item.target_directory,
                ):
                    success(f"  {item.label} restored.")
                else:
                    error(f"  {item.label} restore failed.")
                    failures.append(item.label)

            elif item.service_type == "mongo":
                info("  Restoring MongoDB...")
                if _restore_to_container(
                    runtime,
                    item.container,
                    ["mongorestore", "--archive", "--drop"],
                    item.source_file,
                ):
                    success("  MongoDB restored.")
                else:
                    error("  MongoDB restore failed.")
                    failures.append("MongoDB")

            elif item.service_type == "postgres":
                database = item.database
                assert database is not None
                info(f"  Restoring {item.label}...")
                if _restore_to_container(
                    runtime,
                    item.container,
                    [
                        "psql",
                        "--set",
                        "ON_ERROR_STOP=on",
                        "--single-transaction",
                        "-U",
                        database.user,
                        database.database,
                    ],
                    item.source_file,
                ):
                    success(f"  {item.label} restored.")
                else:
                    error(f"  {item.label} restore failed.")
                    failures.append(item.label)

            else:
                database = item.database
                assert database is not None
                info(f"  Restoring {item.label}...")
                if _restore_to_container(
                    runtime,
                    item.container,
                    _mysql_family_command(
                        database.type,
                        database.database,
                        restore=True,
                    ),
                    item.source_file,
                ):
                    success(f"  {item.label} restored.")
                else:
                    error(f"  {item.label} restore failed.")
                    failures.append(item.label)

    if failures:
        raise click.ClickException(
            "Restore failed for " + ", ".join(failures) + "."
        )
    success("Restore complete.")


@backup.command(name="list")
@click.pass_context
def list_backups(ctx):
    """List available backups."""
    instance: InstanceContext = require_single_instance(ctx)
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
        # Parse legacy local and current UTC managed-backup names.
        name = f.stem.replace(".tar", "")
        parts = name.rsplit("-", 1)
        date = parts[-1] if len(parts) > 1 else "unknown"
        if re.fullmatch(r"\d{8}_\d{6}", date):
            date = f"{date[:4]}-{date[4:6]}-{date[6:8]} {date[9:11]}:{date[11:13]}:{date[13:15]}"
        elif re.fullmatch(r"\d{8}T\d{6}_\d{6}Z", date):
            date = (
                f"{date[:4]}-{date[4:6]}-{date[6:8]} "
                f"{date[9:11]}:{date[11:13]}:{date[13:15]} UTC"
            )
        table.add_row(f.name, f"{size_mb:.1f} MB", date)

    console.print(table)
