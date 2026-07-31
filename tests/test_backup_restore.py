"""Focused regression tests for safe backup and restore behavior."""

import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from src.commands import backup
from src.core.config_manager import save_config
from src.models.config import DatabaseConfig, OpalConfig


class RecordingRuntime:
    name = "podman"

    def __init__(
        self,
        *,
        returncode=0,
        run_stdout="",
        compose_stdout="",
        run_stderr="",
        container_states=None,
        preflight_returncodes=None,
    ):
        self.returncode = returncode
        self.run_stdout = run_stdout
        self.run_stderr = run_stderr
        self.compose_stdout = compose_stdout
        self.container_states = container_states or {}
        self.preflight_returncodes = preflight_returncodes or {}
        self.run_calls = []
        self.compose_calls = []

    def run(self, args, **kwargs):
        self.run_calls.append((args, kwargs))
        returncode = self.returncode
        stderr = self.run_stderr
        if args[0] == "inspect":
            state = self.container_states.get(args[-1], "running")
            if state is None:
                returncode = 1
                stdout = ""
                stderr = "no such container"
            else:
                returncode = 0
                stdout = state
        elif "easy-opal-restore-preflight" in args:
            returncode = self.preflight_returncodes.get(args[1], 0)
            stdout = ""
        elif args[0] == "exec" and args[2:5] == ["stat", "-c", "%u:%g"]:
            returncode = 0
            stdout = "1000:1000\n"
        else:
            stdout = self.run_stdout
        if kwargs.get("text"):
            return subprocess.CompletedProcess(
                args, returncode, stdout, stderr
            )
        return subprocess.CompletedProcess(
            args, returncode, stdout.encode(), stderr.encode()
        )

    def compose(self, args, instance, **kwargs):
        self.compose_calls.append((args, instance, kwargs))
        return subprocess.CompletedProcess(
            args, self.returncode, self.compose_stdout, ""
        )


class LocalDirectoryRuntime:
    """Execute container filesystem operations against local temporary paths."""

    name = "podman"

    def __init__(self, *, fail_copy=False, fail_swap=False, fail_rollback=False):
        self.fail_copy = fail_copy
        self.fail_swap = fail_swap
        self.fail_rollback = fail_rollback
        self.run_calls = []

    def run(self, args, **kwargs):
        self.run_calls.append((args, kwargs))
        if args[0] == "cp":
            if self.fail_copy:
                return subprocess.CompletedProcess(args, 1, b"", b"copy failed")
            source = Path(args[1].removesuffix("/."))
            destination = Path(args[2].split(":", 1)[1])
            destination.mkdir(parents=True, exist_ok=True)
            for child in source.iterdir():
                target = destination / child.name
                if child.is_dir():
                    shutil.copytree(child, target, symlinks=True)
                else:
                    shutil.copy2(child, target, follow_symlinks=False)
            return subprocess.CompletedProcess(args, 0, b"", b"")

        if args[0] == "exec":
            command_index = 1
            if args[1:3] == ["--user", "0"]:
                command_index = 3
            assert args[command_index] == "test-container"
            command = list(args[command_index + 1 :])

            if command[:3] == ["stat", "-c", "%u:%g"]:
                metadata = Path(command[3]).stat()
                output = f"{metadata.st_uid}:{metadata.st_gid}\n"
                if not kwargs.get("text"):
                    output = output.encode()
                return subprocess.CompletedProcess(args, 0, output, "")

            if command[:2] == ["chown", "-R"]:
                return subprocess.CompletedProcess(args, 0, b"", b"")

            if (self.fail_swap or self.fail_rollback) and command[:2] == ["sh", "-c"]:
                command[2] = command[2].replace(
                    "phase=install\nmove_contents",
                    "phase=install\nfalse\nmove_contents",
                    1,
                )
                if self.fail_rollback:
                    command[2] = command[2].replace(
                        'if [ -d "$previous" ]; then\n'
                        '        move_contents "$previous" "$target" || rollback_failed=1',
                        'mkdir "$target/current.txt"\n'
                        '    if [ -d "$previous" ]; then\n'
                        '        move_contents "$previous" "$target" || rollback_failed=1',
                        1,
                    )
            return subprocess.run(command, **kwargs)

        raise AssertionError(f"Unexpected runtime command: {args}")


def _write_backup_archive(tmp_path: Path, manifest: dict, files=None) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "manifest.json").write_text(json.dumps(manifest))
    for name, content in (files or {}).items():
        (payload / name).write_bytes(content)

    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="backup")
    return archive


def _application_tar(directory_name: str) -> bytes:
    payload = b"application data"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(f"{directory_name}/data.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def _mutation_commands(runtime) -> list[list[str]]:
    return [
        args
        for args, _kwargs in runtime.run_calls
        if args[0] == "cp"
        or (
            args[0] == "exec"
            and "easy-opal-restore-preflight" not in args
            and args[2:5] != ["stat", "-c", "%u:%g"]
        )
    ]


def test_scheduled_backup_skips_disabled_feature_without_runtime(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(stack_name="study", backup={"enabled": False}),
        tmp_instance,
    )

    def unexpected_runtime(_instance):
        raise AssertionError("disabled scheduled backup must not resolve a runtime")

    monkeypatch.setattr(backup, "get_runtime", unexpected_runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--scheduled"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert "Automated backups are disabled; skipping." in result.output
    assert not (tmp_instance.root / "backups").exists()


def test_scheduled_backup_rechecks_disabled_feature_after_lock(
    tmp_instance, monkeypatch
):
    initially_enabled = OpalConfig(
        stack_name="study", backup={"enabled": True}
    )
    disabled = OpalConfig(stack_name="study", backup={"enabled": False})
    save_config(initially_enabled, tmp_instance)
    configs = iter([initially_enabled, disabled])
    monkeypatch.setattr(backup, "load_config", lambda _instance: next(configs))

    def unexpected_runtime(_instance):
        raise AssertionError("disabled scheduled backup must not resolve a runtime")

    monkeypatch.setattr(backup, "get_runtime", unexpected_runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--scheduled"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert "disabled while waiting; skipping" in result.output
    assert not (tmp_instance.root / "backups").exists()


def test_scheduled_backup_skips_stopped_stack_without_backup_effects(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(stack_name="study", backup={"enabled": True}),
        tmp_instance,
    )
    runtime = RecordingRuntime(compose_stdout="")
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--scheduled"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert "Stack is stopped; skipping scheduled backup." in result.output
    assert runtime.compose_calls == []
    assert [call[0] for call in runtime.run_calls] == [
        [
            "ps",
            "--filter",
            "label=com.docker.compose.project=study",
            "--format",
            "{{.ID}}",
        ],
        [
            "ps",
            "--filter",
            "label=io.podman.compose.project=study",
            "--format",
            "{{.ID}}",
        ],
    ]
    assert all("-a" not in call[0] for call in runtime.run_calls)
    assert not (tmp_instance.root / "backups").exists()


def test_running_stack_probe_checks_both_engine_labels_without_all_containers():
    runtime = RecordingRuntime(run_stdout="container-id\n")

    assert backup._stack_is_running(runtime, "study")
    assert len(runtime.run_calls) == 2
    assert all(call[0][0] == "ps" for call in runtime.run_calls)
    assert all("-a" not in call[0] for call in runtime.run_calls)
    assert all(call[1]["timeout"] == 30 for call in runtime.run_calls)


def test_database_command_failures_do_not_echo_stderr(tmp_path, monkeypatch):
    sensitive_stderr = "ERROR: failed row contains super-secret-value"
    runtime = RecordingRuntime(returncode=9, run_stderr=sensitive_stderr)
    messages = []
    restore_input = tmp_path / "restore.sql"
    restore_input.write_text("select 1;")
    monkeypatch.setattr(backup, "error", messages.append)

    assert not backup._run_in_container(
        runtime,
        "study-database",
        ["pg_dump", "opaldata"],
        tmp_path / "dump.sql",
    )
    assert not backup._restore_to_container(
        runtime,
        "study-database",
        ["psql", "opaldata"],
        restore_input,
    )

    assert messages == [
        "  Command failed in study-database (exit code 9).",
        "  Restore failed in study-database (exit code 9).",
    ]
    assert sensitive_stderr not in repr(messages)


def test_postgres_backup_is_cleanly_replayable(tmp_instance, monkeypatch):
    config = OpalConfig(
        stack_name="study",
        databases=[
            DatabaseConfig(
                type="postgres", name="analytics", port=5432, database="opaldata"
            )
        ],
    )
    save_config(config, tmp_instance)
    runtime = RecordingRuntime()
    commands = []

    def fake_dump(runtime, container, command, output_path):
        commands.append(command)
        output_path.write_bytes(b"dump")
        return True

    def fake_application_archive(
        runtime, container, source, staging_dir, directory_name, archive_name
    ):
        archive = staging_dir / archive_name
        archive.write_bytes(b"application")
        return archive

    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(backup, "_run_in_container", fake_dump)
    monkeypatch.setattr(
        backup, "_archive_container_directory", fake_application_archive
    )

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--output", str(tmp_instance.root / "study.tar.gz")],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert [
        "pg_dump",
        "--clean",
        "--if-exists",
        "-U",
        "opal",
        "opaldata",
    ] in commands


def test_mysql_family_backups_use_native_clients_without_host_argv_secrets(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(
            stack_name="study",
            databases=[
                DatabaseConfig(type="mysql", name="mysql-db", port=3306),
                DatabaseConfig(type="mariadb", name="maria-db", port=3307),
            ],
        ),
        tmp_instance,
    )
    tmp_instance.secrets_path.write_text(
        "MYSQL_DB_PASSWORD=mysql-super-secret\n"
        "MARIA_DB_PASSWORD=maria-super-secret\n"
    )
    runtime = RecordingRuntime()

    def fake_application_archive(
        runtime, container, source, staging_dir, directory_name, archive_name
    ):
        archive = staging_dir / archive_name
        archive.write_bytes(b"application")
        return archive

    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(
        backup, "_archive_container_directory", fake_application_archive
    )

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--output", str(tmp_instance.root / "study.tar.gz")],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    calls = {call[0][1]: call[0] for call in runtime.run_calls}
    mysql = calls["study-mysql-db"]
    mariadb = calls["study-maria-db"]
    assert mysql[:4] == ["exec", "study-mysql-db", "sh", "-c"]
    assert "MYSQL_ROOT_PASSWORD" in mysql[4]
    assert mysql[6:9] == ["mysqldump", "--single-transaction", "-u"]
    assert mariadb[:4] == ["exec", "study-maria-db", "sh", "-c"]
    assert "MARIADB_ROOT_PASSWORD" in mariadb[4]
    assert mariadb[6:9] == ["mariadb-dump", "--single-transaction", "-u"]
    rendered = repr([mysql, mariadb])
    assert "mysql-super-secret" not in rendered
    assert "maria-super-secret" not in rendered
    assert "-e" not in mysql
    assert "-e" not in mariadb


def test_successful_backup_is_private_and_leaves_no_staging(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study", flavor="armadillo"), tmp_instance)
    runtime = RecordingRuntime()
    staging_modes = []

    def fake_application_archive(
        runtime, container, source, staging_dir, directory_name, archive_name
    ):
        staging_modes.append(stat.S_IMODE(staging_dir.stat().st_mode))
        archive = staging_dir / archive_name
        archive.write_bytes(b"application")
        return archive

    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(
        backup, "_archive_container_directory", fake_application_archive
    )

    result = CliRunner().invoke(
        backup.backup,
        ["create"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    backups_dir = tmp_instance.root / "backups"
    archives = list(backups_dir.glob("*.tar.gz"))
    assert len(archives) == 1
    assert stat.S_IMODE(backups_dir.stat().st_mode) == 0o700
    assert staging_modes == [0o700]
    assert stat.S_IMODE(archives[0].stat().st_mode) == 0o600
    assert list(backups_dir.iterdir()) == archives


def test_archive_failure_preserves_destination_and_cleans_temporary_files(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study", flavor="armadillo"), tmp_instance)
    runtime = RecordingRuntime()
    destination = tmp_instance.root / "existing.tar.gz"
    destination.write_bytes(b"previous archive")
    original_open = backup.tarfile.open

    def fake_application_archive(
        runtime, container, source, staging_dir, directory_name, archive_name
    ):
        archive = staging_dir / archive_name
        archive.write_bytes(b"application")
        return archive

    def fail_archive_open(name, mode="r", *args, **kwargs):
        if mode == "w:gz":
            raise tarfile.TarError("archive write failed")
        return original_open(name, mode, *args, **kwargs)

    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(
        backup, "_archive_container_directory", fake_application_archive
    )
    monkeypatch.setattr(backup.tarfile, "open", fail_archive_open)

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--output", str(destination)],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Could not create backup archive" in result.output
    assert destination.read_bytes() == b"previous archive"
    assert list((tmp_instance.root / "backups").iterdir()) == []
    assert list(tmp_instance.root.glob(".existing.tar.gz.*.tmp")) == []


def test_scheduled_retention_only_removes_managed_stack_backups(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(
            stack_name="study",
            flavor="armadillo",
            backup={"enabled": True, "keep": 1},
        ),
        tmp_instance,
    )
    backups_dir = backup._backups_dir(tmp_instance)
    old_managed = backups_dir / "study-20250101_000000.tar.gz"
    other_stack = backups_dir / "other-20250101_000000.tar.gz"
    manual = backups_dir / "manual.tar.gz"
    for archive in (old_managed, other_stack, manual):
        archive.write_bytes(b"existing")
    os.utime(old_managed, ns=(1, 1))
    runtime = RecordingRuntime(run_stdout="running-container\n")

    def fake_application_archive(
        runtime, container, source, staging_dir, directory_name, archive_name
    ):
        archive = staging_dir / archive_name
        archive.write_bytes(b"application")
        return archive

    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(
        backup, "_archive_container_directory", fake_application_archive
    )

    result = CliRunner().invoke(
        backup.backup,
        ["create", "--scheduled"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert not old_managed.exists()
    assert other_stack.read_bytes() == b"existing"
    assert manual.read_bytes() == b"existing"
    assert len(backup._managed_backups(backups_dir, "study")) == 1


def test_managed_backups_order_mixed_name_formats_by_modification_time(tmp_path):
    legacy = tmp_path / "study-20260731_235959.tar.gz"
    current = tmp_path / "study-20260731T000000_000000Z.tar.gz"
    legacy.write_bytes(b"legacy")
    current.write_bytes(b"current")
    os.utime(legacy, ns=(1, 1))
    os.utime(current, ns=(2, 2))

    assert backup._managed_backups(tmp_path, "study") == [current, legacy]


def test_backup_list_formats_current_utc_timestamp(tmp_instance):
    archive = (
        backup._backups_dir(tmp_instance)
        / "study-20260731T123456_123456Z.tar.gz"
    )
    archive.write_bytes(b"archive")

    result = CliRunner().invoke(
        backup.backup,
        ["list"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert "2026-07-31 12:34:56 UTC" in result.output


def test_postgres_restore_uses_single_transaction_and_on_error_stop(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(
            stack_name="study",
            databases=[
                DatabaseConfig(type="postgres", name="analytics", port=5432)
            ],
        ),
        tmp_instance,
    )
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "study-backup",
            "stack_name": "study",
            "services": [
                {"type": "postgres", "name": "analytics", "file": "analytics.sql"}
            ],
        },
        {"analytics.sql": b"select 1;"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    restore_command = next(
        call[0] for call in runtime.run_calls if call[0][:2] == ["exec", "-i"]
    )
    assert restore_command == [
        "exec",
        "-i",
        "study-analytics",
        "psql",
        "--set",
        "ON_ERROR_STOP=on",
        "--single-transaction",
        "-U",
        "opal",
        "opaldata",
    ]


def test_cancelled_restore_does_not_resolve_or_probe_runtime(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "study-backup",
            "stack_name": "study",
            "services": [{"type": "mongo", "file": "mongo.archive"}],
        },
        {"mongo.archive": b"archive"},
    )
    monkeypatch.setattr(
        backup,
        "get_runtime",
        lambda _instance: (_ for _ in ()).throw(
            AssertionError("cancelled restore must not resolve a runtime")
        ),
    )

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive)],
        input="n\n",
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert "Restore this backup?" in result.output


@pytest.mark.parametrize(
    ("database_type", "client", "password_variable"),
    [
        ("mysql", "mysql", "MYSQL_ROOT_PASSWORD"),
        ("mariadb", "mariadb", "MARIADB_ROOT_PASSWORD"),
    ],
)
def test_mysql_family_restore_uses_container_password_and_native_client(
    tmp_instance, monkeypatch, database_type, client, password_variable
):
    save_config(
        OpalConfig(
            stack_name="study",
            databases=[
                DatabaseConfig(
                    type=database_type,
                    name="analytics",
                    port=3306,
                    database="opaldata",
                )
            ],
        ),
        tmp_instance,
    )
    tmp_instance.secrets_path.write_text("ANALYTICS_PASSWORD=host-super-secret\n")
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "study-backup",
            "stack_name": "study",
            "services": [
                {
                    "type": database_type,
                    "name": "analytics",
                    "file": "analytics.sql",
                }
            ],
        },
        {"analytics.sql": b"select 1;"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    command = next(
        call[0] for call in runtime.run_calls if call[0][:2] == ["exec", "-i"]
    )
    assert command[:5] == ["exec", "-i", "study-analytics", "sh", "-c"]
    assert password_variable in command[5]
    assert command[7:] == [client, "-u", "root", "opaldata"]
    assert "host-super-secret" not in repr(command)
    assert "-e" not in command


@pytest.mark.parametrize(
    "services",
    [
        [],
        [{}],
        [{"type": "unknown", "file": "data"}],
        [{"type": "mongo", "file": ""}],
        [{"type": "postgres", "file": "database.sql"}],
    ],
)
def test_restore_rejects_invalid_service_manifests(
    tmp_instance, monkeypatch, services
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {"name": "invalid", "stack_name": "study", "services": services},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Invalid backup manifest" in result.output
    assert runtime.run_calls == []


@pytest.mark.parametrize(
    ("config", "flavor", "service", "message"),
    [
        (
            OpalConfig(stack_name="study"),
            "armadillo",
            {"type": "armadillo", "file": "application.tar"},
            "incompatible with this 'opal' instance",
        ),
        (
            OpalConfig(stack_name="study"),
            "opal",
            {"type": "armadillo", "file": "application.tar"},
            "incompatible with backup flavor 'opal'",
        ),
        (
            OpalConfig(stack_name="study", flavor="armadillo"),
            "armadillo",
            {"type": "mongo", "file": "mongo.archive"},
            "incompatible with backup flavor 'armadillo'",
        ),
    ],
)
def test_restore_rejects_flavor_and_service_mismatches_before_runtime(
    tmp_instance, monkeypatch, config, flavor, service, message
):
    save_config(config, tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "incompatible",
            "flavor": flavor,
            "services": [service],
        },
        {service["file"]: b"payload"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert message in result.output
    assert runtime.run_calls == []


@pytest.mark.parametrize(
    ("services", "files", "message"),
    [
        (
            [
                {"type": "mongo", "file": "first.archive"},
                {"type": "mongo", "file": "second.archive"},
            ],
            {"first.archive": b"first", "second.archive": b"second"},
            "duplicates restore target 'mongo'",
        ),
        (
            [
                {"type": "postgres", "name": "analytics", "file": "first.sql"},
                {"type": "mysql", "name": "analytics", "file": "second.sql"},
            ],
            {"first.sql": b"first", "second.sql": b"second"},
            "duplicates restore target 'analytics'",
        ),
        (
            [
                {"type": "mongo", "file": "shared.data"},
                {"type": "opal", "file": "shared.data"},
            ],
            {"shared.data": b"shared"},
            "reuses data file 'shared.data'",
        ),
    ],
)
def test_restore_rejects_duplicate_targets_and_files_before_runtime(
    tmp_instance, monkeypatch, services, files, message
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {"name": "duplicate", "services": services},
        files,
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert message in result.output
    assert runtime.run_calls == []


@pytest.mark.parametrize(
    ("databases", "service_type", "message"),
    [
        ([], "postgres", "is not configured"),
        (
            [
                DatabaseConfig(
                    type="postgres",
                    name="analytics",
                    port=5432,
                    external=True,
                    host="database.example",
                )
            ],
            "postgres",
            "is external and cannot be restored",
        ),
        (
            [DatabaseConfig(type="mysql", name="analytics", port=3306)],
            "postgres",
            "configured as 'mysql', not 'postgres'",
        ),
        (
            [
                DatabaseConfig(type="postgres", name="analytics", port=5432),
                DatabaseConfig(type="postgres", name="analytics", port=5433),
            ],
            "postgres",
            "configured more than once",
        ),
    ],
)
def test_restore_validates_database_target_before_runtime(
    tmp_instance, monkeypatch, databases, service_type, message
):
    save_config(
        OpalConfig(stack_name="study", databases=databases),
        tmp_instance,
    )
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "database",
            "services": [
                {
                    "type": service_type,
                    "name": "analytics",
                    "file": "analytics.sql",
                }
            ],
        },
        {"analytics.sql": b"select 1;"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert message in result.output
    assert runtime.run_calls == []


def test_restore_rejects_container_target_collision_before_runtime(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(
            stack_name="study",
            databases=[
                DatabaseConfig(type="postgres", name="mongo", port=5432)
            ],
        ),
        tmp_instance,
    )
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "collision",
            "services": [
                {"type": "mongo", "file": "mongo.archive"},
                {"type": "postgres", "name": "mongo", "file": "mongo.sql"},
            ],
        },
        {"mongo.archive": b"mongo", "mongo.sql": b"select 1;"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "both target container 'study-mongo'" in result.output
    assert runtime.run_calls == []


def test_restore_validates_all_application_archives_before_runtime(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "corrupt-application",
            "services": [
                {"type": "mongo", "file": "mongo.archive"},
                {"type": "opal", "file": "opal-srv.tar"},
            ],
        },
        {"mongo.archive": b"mongo", "opal-srv.tar": b"not a tar archive"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "invalid Opal data archive" in result.output
    assert runtime.run_calls == []


def test_restore_rejects_outer_archive_over_declared_size_limit_before_runtime(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "oversized",
            "services": [{"type": "mongo", "file": "mongo.archive"}],
        },
        {"mongo.archive": b"mongo"},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "_MAX_RESTORE_ARCHIVE_BYTES", 1)
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "backup archive declares more than 1 bytes" in result.output
    assert runtime.run_calls == []


def test_restore_rejects_inner_archive_over_member_limit_before_runtime(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w") as archive:
        for index in range(4):
            payload = b"data"
            member = tarfile.TarInfo(f"opal-srv/data-{index}.txt")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    backup_archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "too-many-files",
            "services": [{"type": "opal", "file": "opal-srv.tar"}],
        },
        {"opal-srv.tar": inner.getvalue()},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "_MAX_RESTORE_ARCHIVE_MEMBERS", 3)
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(backup_archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Opal data archive has too many members (4 > 3)" in result.output
    assert runtime.run_calls == []


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (None, "could not find container 'study-mongo'"),
        ("exited", "current state: exited"),
    ],
)
def test_restore_requires_existing_running_containers_before_mutation(
    tmp_instance, monkeypatch, state, message
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = _write_backup_archive(
        tmp_instance.root,
        {"name": "mongo", "services": [{"type": "mongo", "file": "mongo.archive"}]},
        {"mongo.archive": b"mongo"},
    )
    runtime = RecordingRuntime(container_states={"study-mongo": state})
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert message in result.output
    assert _mutation_commands(runtime) == []


def test_restore_checks_every_tool_before_first_mutation(tmp_instance, monkeypatch):
    save_config(
        OpalConfig(
            stack_name="study",
            databases=[
                DatabaseConfig(type="postgres", name="analytics", port=5432)
            ],
        ),
        tmp_instance,
    )
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "tools",
            "services": [
                {"type": "mongo", "file": "mongo.archive"},
                {
                    "type": "postgres",
                    "name": "analytics",
                    "file": "analytics.sql",
                },
            ],
        },
        {"mongo.archive": b"mongo", "analytics.sql": b"select 1;"},
    )
    runtime = RecordingRuntime(
        preflight_returncodes={"study-analytics": 20}
    )
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "required restore tool is unavailable" in result.output
    assert [call[0][-1] for call in runtime.run_calls[:2]] == [
        "study-mongo",
        "study-analytics",
    ]
    assert _mutation_commands(runtime) == []


@pytest.mark.parametrize(
    ("database_type", "password_variable"),
    [("mysql", "MYSQL_ROOT_PASSWORD"), ("mariadb", "MARIADB_ROOT_PASSWORD")],
)
def test_restore_preflights_container_password_without_exposing_secret(
    tmp_instance, monkeypatch, database_type, password_variable
):
    save_config(
        OpalConfig(
            stack_name="study",
            databases=[
                DatabaseConfig(type=database_type, name="analytics", port=3306)
            ],
        ),
        tmp_instance,
    )
    secret = "must-not-appear-on-host-command"
    tmp_instance.secrets_path.write_text(f"ANALYTICS_PASSWORD={secret}\n")
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "password",
            "services": [
                {
                    "type": database_type,
                    "name": "analytics",
                    "file": "analytics.sql",
                }
            ],
        },
        {"analytics.sql": b"select 1;"},
    )
    runtime = RecordingRuntime(
        preflight_returncodes={"study-analytics": 21}
    )
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "root password environment is unavailable" in result.output
    rendered_commands = repr([call[0] for call in runtime.run_calls])
    assert password_variable in rendered_commands
    assert secret not in rendered_commands
    assert "-e" not in [arg for call in runtime.run_calls for arg in call[0]]
    assert _mutation_commands(runtime) == []


def test_armadillo_restore_runs_all_preflight_before_mutation(
    tmp_instance, monkeypatch
):
    save_config(
        OpalConfig(stack_name="study", flavor="armadillo"),
        tmp_instance,
    )
    archive = _write_backup_archive(
        tmp_instance.root,
        {
            "name": "armadillo",
            "flavor": "armadillo",
            "services": [
                {"type": "armadillo", "file": "armadillo-data.tar"}
            ],
        },
        {"armadillo-data.tar": _application_tar("armadillo-data")},
    )
    runtime = RecordingRuntime()
    monkeypatch.setattr(backup, "get_runtime", lambda _instance: runtime)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    first_mutation = next(
        index
        for index, (args, _kwargs) in enumerate(runtime.run_calls)
        if args in _mutation_commands(runtime)
    )
    assert runtime.run_calls[0][0][0] == "inspect"
    assert "easy-opal-restore-preflight" in runtime.run_calls[1][0]
    assert first_mutation == 3
    assert runtime.run_calls[first_mutation][0][:5] == [
        "exec",
        "--user",
        "0",
        "study-armadillo",
        "mkdir",
    ]


def test_application_directory_restore_replaces_obsolete_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "new.txt").write_text("new")
    (source / ".hidden").write_text("hidden")
    target = tmp_path / "target"
    target.mkdir()
    (target / "obsolete.txt").write_text("obsolete")
    (target / "new.txt").write_text("old")
    runtime = LocalDirectoryRuntime()

    restored = backup._replace_container_directory(
        runtime, "test-container", source, str(target)
    )

    assert restored
    assert sorted(path.name for path in target.iterdir()) == [".hidden", "new.txt"]
    assert (target / "new.txt").read_text() == "new"
    expected_owner = f"{target.stat().st_uid}:{target.stat().st_gid}"
    assert any(
        args[:7]
        == [
            "exec",
            "--user",
            "0",
            "test-container",
            "chown",
            "-R",
            expected_owner,
        ]
        for args, _kwargs in runtime.run_calls
    )


def test_application_directory_restore_keeps_live_data_when_staging_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "new.txt").write_text("new")
    target = tmp_path / "target"
    target.mkdir()
    (target / "current.txt").write_text("current")
    runtime = LocalDirectoryRuntime(fail_copy=True)

    restored = backup._replace_container_directory(
        runtime, "test-container", source, str(target)
    )

    assert not restored
    assert sorted(path.name for path in target.iterdir()) == ["current.txt"]
    assert (target / "current.txt").read_text() == "current"


def test_application_directory_restore_rolls_back_when_swap_fails(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "new.txt").write_text("new")
    target = tmp_path / "target"
    target.mkdir()
    (target / "current.txt").write_text("current")
    runtime = LocalDirectoryRuntime(fail_swap=True)

    restored = backup._replace_container_directory(
        runtime, "test-container", source, str(target)
    )

    assert not restored
    assert sorted(path.name for path in target.iterdir()) == ["current.txt"]
    assert (target / "current.txt").read_text() == "current"


def test_application_directory_restore_preserves_recovery_data_when_rollback_fails(
    tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "new.txt").write_text("new")
    target = tmp_path / "target"
    target.mkdir()
    (target / "current.txt").write_text("current")
    (target / "other.txt").write_text("other")
    runtime = LocalDirectoryRuntime(fail_rollback=True)

    with pytest.raises(click.ClickException) as exc_info:
        backup._replace_container_directory(
            runtime, "test-container", source, str(target)
        )

    previous = list(target.glob(".easy-opal-restore-previous-*"))
    stage = list(target.glob(".easy-opal-restore-stage-*"))
    assert len(previous) == 1
    assert len(stage) == 1
    assert (previous[0] / "current.txt").read_text() == "current"
    assert (stage[0] / "new.txt").read_text() == "new"
    assert str(previous[0]) in str(exc_info.value)
    assert str(stage[0]) in str(exc_info.value)
    assert "automatic rollback was incomplete" in str(exc_info.value)
