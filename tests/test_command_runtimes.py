"""Command-level checks for engine-neutral container runtime usage."""

import json
import io
import re
import subprocess
import tarfile
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from src.commands import backup, config, diagnose, doctor, instances, profiles, volumes
from src.core.config_manager import load_config, save_config
from src.core.docker import CertificateAcquisitionResult
from src.core.secrets_manager import load_secrets, save_secrets
from src.models.config import DatabaseConfig, OpalConfig, WatchtowerConfig


class FakeRuntime:
    name = "podman"
    compose_command = ("podman", "compose")
    env = {}

    def __init__(self, *, stdout="", stderr="", returncode=0):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.run_calls = []
        self.compose_calls = []

    def run(self, args, **kwargs):
        self.run_calls.append((args, kwargs))
        return self.result

    def _env(self):
        return self.env.copy()

    def compose(self, args, instance, project_name=None, **kwargs):
        self.compose_calls.append((args, instance, project_name, kwargs))
        return self.result


def test_instance_status_uses_selected_compose_and_accepts_json_array(tmp_instance, monkeypatch):
    output = json.dumps([
        {"Name": "study-opal", "State": "running", "Health": "healthy"},
        {"Name": "study-mongo", "State": "running"},
    ])
    runtime = FakeRuntime(stdout=output)
    monkeypatch.setattr(instances, "get_runtime", lambda instance: runtime)

    statuses = instances._get_container_status(tmp_instance, "study")

    assert statuses == {"opal": "running (healthy)", "mongo": "running"}
    assert runtime.compose_calls[0][:3] == (["ps", "--format", "json"], tmp_instance, "study")


def test_instance_status_accepts_podman_names_array(tmp_instance, monkeypatch):
    output = json.dumps(
        [
            {
                "Names": ["study-opal"],
                "State": "running",
                "Status": "Up 5 minutes (healthy)",
            }
        ]
    )
    runtime = FakeRuntime(stdout=output)
    monkeypatch.setattr(instances, "get_runtime", lambda instance: runtime)

    statuses = instances._get_container_status(tmp_instance, "study")

    assert statuses == {"opal": "running (healthy)"}


def test_diagnose_uses_selected_compose_and_accepts_json_array(tmp_instance, sample_config, monkeypatch):
    output = json.dumps([{"Name": "test-opal-opal", "State": "running"}])
    runtime = FakeRuntime(stdout=output)
    monkeypatch.setattr(diagnose, "get_runtime", lambda instance: runtime)

    result = diagnose._check_containers(tmp_instance, sample_config)

    assert result.status == "pass"
    assert runtime.compose_calls[0][:3] == (
        ["ps", "--format", "json"],
        tmp_instance,
        sample_config.stack_name,
    )


def test_volumes_use_selected_engine_and_accept_json_array(tmp_instance, monkeypatch):
    runtime = FakeRuntime(stdout=json.dumps([{"Name": "study-data", "Driver": "local"}]))
    monkeypatch.setattr(volumes, "get_runtime", lambda instance: runtime)

    result = volumes._get_project_volumes(tmp_instance, "study")

    assert result == [{"Name": "study-data", "Driver": "local"}]
    assert runtime.run_calls[0][0] == [
        "volume",
        "ls",
        "--format",
        "json",
        "--filter",
        "label=com.docker.compose.project=study",
    ]


def test_volumes_list_query_failure_is_nonzero(tmp_instance, monkeypatch):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    runtime = FakeRuntime(returncode=1, stderr="permission denied")
    monkeypatch.setattr(volumes, "get_runtime", lambda instance: runtime)

    result = CliRunner().invoke(
        volumes.volumes,
        ["list"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Could not list project volumes" in result.output
    assert "No volumes found" not in result.output


def test_volumes_prune_query_failure_is_nonzero(tmp_instance, monkeypatch):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    runtime = FakeRuntime(returncode=1, stderr="permission denied")
    runtime.compose = lambda *args, **kwargs: subprocess.CompletedProcess(
        [], 0, "", ""
    )
    monkeypatch.setattr(volumes, "get_runtime", lambda instance: runtime)

    result = CliRunner().invoke(
        volumes.volumes,
        ["prune", "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Could not list project volumes" in result.output
    assert "No project volumes found" not in result.output


def test_remove_database_volume_identification_failure_is_nonzero(
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
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(config, "list_project_volumes", lambda *args: [])
    apply_calls = []
    monkeypatch.setattr(
        config,
        "_apply_config",
        lambda *args, **kwargs: apply_calls.append((args, kwargs)),
    )

    result = CliRunner().invoke(
        config.config,
        ["remove-database", "analytics", "--delete-volume", "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Could not identify one physical volume" in result.output
    assert apply_calls == []
    assert [db.name for db in load_config(tmp_instance).databases] == ["analytics"]


def test_remove_database_volume_delete_failure_is_nonzero(
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
    runtime = FakeRuntime(returncode=1, stderr="volume is in use")
    monkeypatch.setattr(config, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(
        config,
        "list_project_volumes",
        lambda *args: ["study_study-analytics-data"],
    )
    monkeypatch.setattr(
        config,
        "_apply_config",
        lambda cfg, instance: save_config(cfg, instance),
    )

    result = CliRunner().invoke(
        config.config,
        ["remove-database", "analytics", "--delete-volume", "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Could not delete volume" in result.output
    assert "volume is in use" in result.output
    assert "Run 'easy-opal restart'" not in result.output


def test_backup_exec_arguments_do_not_include_engine(tmp_instance):
    runtime = FakeRuntime(stderr=b"")
    output = tmp_instance.root / "dump.archive"

    assert backup._run_in_container(
        runtime,
        "study-mongo",
        ["mongodump", "--archive"],
        output,
    )
    assert runtime.run_calls[0][0] == [
        "exec",
        "study-mongo",
        "mongodump",
        "--archive",
    ]


def test_backup_restore_arguments_do_not_include_engine(tmp_instance):
    runtime = FakeRuntime(stderr=b"")
    sql = tmp_instance.root / "database.sql"
    sql.write_bytes(b"select 1;")

    assert backup._restore_to_container(
        runtime,
        "study-mysql",
        ["mysql", "-u", "root", "opaldata"],
        sql,
    )
    assert runtime.run_calls[0][0] == [
        "exec",
        "-i",
        "study-mysql",
        "mysql",
        "-u",
        "root",
        "opaldata",
    ]


def test_backup_restore_failure_is_nonzero(tmp_instance, monkeypatch):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    payload = tmp_instance.root / "backup-payload"
    payload.mkdir()
    (payload / "manifest.json").write_text(
        json.dumps(
            {
                "name": "study-backup",
                "stack_name": "study",
                "opal_version": "latest",
                "services": [{"type": "mongo", "file": "mongo.archive"}],
            }
        )
    )
    (payload / "mongo.archive").write_bytes(b"dump")
    archive = tmp_instance.root / "study-backup.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload, arcname="study-backup")

    runtime = FakeRuntime(returncode=1, stderr=b"restore failed")
    monkeypatch.setattr(backup, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(backup, "_preflight_restore_targets", lambda *_args: None)

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "Restore failed for MongoDB" in result.output
    assert "Restore complete" not in result.output


def test_backup_restore_rejects_path_traversal(tmp_instance, monkeypatch):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    archive = tmp_instance.root / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo("../escaped.txt")
        content = b"should stay contained"
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))

    monkeypatch.setattr(backup, "get_runtime", lambda instance: FakeRuntime())
    escaped = tmp_instance.root.parent / "escaped.txt"

    result = CliRunner().invoke(
        backup.backup,
        ["restore", str(archive), "--yes"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert not escaped.exists()


def test_profile_status_uses_selected_engine(tmp_instance, monkeypatch):
    runtime = FakeRuntime(stdout="running\n")
    monkeypatch.setattr(profiles, "get_runtime", lambda instance: runtime)

    status = profiles._get_container_status(tmp_instance, "study", "rock")

    assert status == "running"
    assert runtime.run_calls[0][0] == [
        "inspect",
        "--format",
        "{{.State.Status}}",
        "study-rock",
    ]


def test_profile_status_reports_unavailable_bound_engine(
    tmp_instance, monkeypatch
):
    def unavailable(_instance):
        raise profiles.RuntimeSelectionError("Podman machine is stopped")

    monkeypatch.setattr(profiles, "get_runtime", unavailable)

    assert (
        profiles._get_container_status(tmp_instance, "study", "rock")
        == "runtime unavailable"
    )


def test_profile_spec_preserves_private_registry_port():
    parsed = profiles._parse_profile_spec(
        "registry.example.org:5000/team/rock:2.1:rock-private", "latest"
    )

    assert parsed.image == "registry.example.org:5000/team/rock"
    assert parsed.tag == "2.1"
    assert parsed.name == "rock-private"


def test_profile_pull_timeout_is_bounded_and_reported():
    class HangingRuntime:
        def pull(self, image, **kwargs):
            assert kwargs["timeout"] == profiles.PULL_TIMEOUT_SECONDS
            raise subprocess.TimeoutExpired(
                ["podman", "pull", image], kwargs["timeout"]
            )

    result, detail = profiles._pull_image(
        HangingRuntime(), "docker.io/datashield/rock-base:latest"
    )

    assert result is None
    assert "timed out" in detail


def test_doctor_reports_selected_engine_and_compose(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(doctor, "get_runtime", lambda instance=None: runtime)

    assert doctor._check_container_runtime().detail == "podman (service reachable)"
    assert doctor._check_compose().detail == "podman compose"


def test_automatic_updates_are_valid_with_podman(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(), tmp_instance)
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda instance: runtime)
    monkeypatch.setattr(config, "generate_compose", lambda *args: None)
    monkeypatch.setattr(
        config, "preflight_enabled_schedules", lambda *args: None
    )
    monkeypatch.setattr(config, "reconcile_schedules", lambda *args: None)
    changed = OpalConfig(watchtower=WatchtowerConfig(enabled=True))

    config._apply_config(changed, tmp_instance)

    assert load_config(tmp_instance).watchtower.enabled is True


def test_config_generation_failure_restores_all_artifacts(
    tmp_instance, monkeypatch
):
    previous = OpalConfig(
        stack_name="study", opal_version="old", ssl={"strategy": "none"}
    )
    save_config(previous, tmp_instance)
    tmp_instance.compose_path.write_text("old compose\n")
    nginx_path = tmp_instance.nginx_conf_dir / "nginx.conf"
    nginx_path.parent.mkdir(parents=True, exist_ok=True)
    nginx_path.write_text("old nginx\n")
    tmp_instance.secrets_path.write_text("OLD_SECRET=value\n")

    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(config, "preflight_enabled_schedules", lambda *_args: None)
    schedule_calls = []
    monkeypatch.setattr(
        config,
        "reconcile_schedules",
        lambda *_args: schedule_calls.append(True),
    )

    def write_nginx(_cfg, instance):
        (instance.nginx_conf_dir / "nginx.conf").write_text("new nginx\n")

    def fail_compose(_cfg, instance):
        instance.compose_path.write_text("new compose\n")
        instance.secrets_path.write_text("NEW_SECRET=value\n")
        raise OSError("simulated generation failure")

    monkeypatch.setattr(config, "generate_nginx_config", write_nginx)
    monkeypatch.setattr(config, "generate_compose", fail_compose)
    changed = previous.model_copy(deep=True)
    changed.opal_version = "new"

    with pytest.raises(click.ClickException, match="previous files"):
        config._apply_config(changed, tmp_instance)

    assert load_config(tmp_instance).opal_version == "old"
    assert tmp_instance.compose_path.read_text() == "old compose\n"
    assert nginx_path.read_text() == "old nginx\n"
    assert tmp_instance.secrets_path.read_text() == "OLD_SECRET=value\n"
    assert schedule_calls == []


def test_config_schedule_failure_restores_generated_files_and_schedule(
    tmp_instance, monkeypatch
):
    previous = OpalConfig(
        stack_name="study", opal_version="old", ssl={"strategy": "none"}
    )
    save_config(previous, tmp_instance)
    tmp_instance.compose_path.write_text("old compose\n")

    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(config, "preflight_enabled_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "generate_nginx_config", lambda *_args: None)
    monkeypatch.setattr(
        config,
        "generate_compose",
        lambda _cfg, instance: instance.compose_path.write_text("new compose\n"),
    )
    reconciled_versions = []

    def reconcile(_instance, _runtime, cfg):
        reconciled_versions.append(cfg.opal_version)
        if cfg.opal_version == "new":
            raise config.AutoUpdateScheduleError("simulated scheduler failure")

    monkeypatch.setattr(config, "reconcile_schedules", reconcile)
    changed = previous.model_copy(deep=True)
    changed.opal_version = "new"

    with pytest.raises(click.ClickException, match="previous files"):
        config._apply_config(changed, tmp_instance)

    assert reconciled_versions == ["new", "old"]
    assert load_config(tmp_instance).opal_version == "old"
    assert tmp_instance.compose_path.read_text() == "old compose\n"


def test_change_password_generation_failure_restores_previous_secret(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    save_secrets({"OPAL_ADMIN_PASSWORD": "old-password"}, tmp_instance)
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(config, "preflight_enabled_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "generate_nginx_config", lambda *_args: None)
    monkeypatch.setattr(
        config,
        "generate_compose",
        lambda *_args: (_ for _ in ()).throw(OSError("compose failed")),
    )

    result = CliRunner().invoke(
        config.config,
        ["change-password", "new-password"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert load_secrets(tmp_instance)["OPAL_ADMIN_PASSWORD"] == "old-password"


def test_agate_preflight_failure_does_not_write_smtp_secret(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study"), tmp_instance)
    save_secrets({"SMTP_PASSWORD": "old-password"}, tmp_instance)
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(
        config,
        "preflight_enabled_schedules",
        lambda *_args: (_ for _ in ()).throw(
            config.AutoUpdateScheduleError("preflight failed")
        ),
    )

    result = CliRunner().invoke(
        config.config,
        ["agate", "enable", "--smtp-password", "new-password"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert load_secrets(tmp_instance)["SMTP_PASSWORD"] == "old-password"


def test_config_apply_rejects_stale_read_modify_write(
    tmp_instance, monkeypatch
):
    save_config(OpalConfig(stack_name="study", opal_version="initial"), tmp_instance)
    stale = load_config(tmp_instance)
    stale.opal_version = "stale-writer"
    concurrent = load_config(tmp_instance)
    concurrent.opal_version = "concurrent-writer"
    save_config(concurrent, tmp_instance)
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)

    with pytest.raises(click.ClickException, match="Configuration changed"):
        config._apply_config(stale, tmp_instance)

    assert load_config(tmp_instance).opal_version == "concurrent-writer"


def test_change_ssl_snapshots_nginx_before_removing_it(
    tmp_instance, monkeypatch
):
    previous = OpalConfig(
        stack_name="study", ssl={"strategy": "self-signed"}
    )
    save_config(previous, tmp_instance)
    nginx_path = tmp_instance.nginx_conf_dir / "nginx.conf"
    nginx_path.parent.mkdir(parents=True, exist_ok=True)
    nginx_path.write_text("old nginx\n")

    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(config, "preflight_enabled_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "reconcile_schedules", lambda *_args: None)

    def remove_nginx(_cfg, instance):
        (instance.nginx_conf_dir / "nginx.conf").unlink()

    monkeypatch.setattr(config, "generate_nginx_config", remove_nginx)
    monkeypatch.setattr(
        config,
        "generate_compose",
        lambda *_args: (_ for _ in ()).throw(OSError("simulated failure")),
    )

    result = CliRunner().invoke(
        config.config,
        ["change-ssl", "none"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "previous files and schedules restored" in result.output
    assert load_config(tmp_instance).ssl.strategy.value == "self-signed"
    assert nginx_path.read_text() == "old nginx\n"


def test_change_ssl_letsencrypt_failure_restores_previous_strategy(
    tmp_instance, monkeypatch
):
    previous = OpalConfig(
        stack_name="study",
        hosts=["opal.example.org"],
        ssl={"strategy": "self-signed"},
    )
    save_config(previous, tmp_instance)
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(config, "preflight_enabled_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "reconcile_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "generate_nginx_config", lambda *_args: None)
    monkeypatch.setattr(config, "generate_compose", lambda *_args: None)
    monkeypatch.setattr(
        config,
        "obtain_letsencrypt_certificate",
        lambda *_args: CertificateAcquisitionResult(False, True),
    )
    restored_nginx = []
    monkeypatch.setattr(
        config,
        "restore_running_nginx",
        lambda cfg, _instance: restored_nginx.append(cfg.ssl.strategy.value)
        or True,
    )

    result = CliRunner().invoke(
        config.config,
        ["change-ssl", "letsencrypt", "--ssl-email", "admin@example.org"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 1
    assert "previous SSL configuration restored" in result.output
    assert load_config(tmp_instance).ssl.strategy.value == "self-signed"
    assert restored_nginx == ["self-signed"]


def test_manual_certificate_copy_forces_private_key_permissions(
    tmp_instance, tmp_path, monkeypatch
):
    previous = OpalConfig(stack_name="study", ssl={"strategy": "none"})
    save_config(previous, tmp_instance)
    cert_source = tmp_path / "source.crt"
    key_source = tmp_path / "source.key"
    cert_source.write_text("certificate")
    key_source.write_text("private key")
    key_source.chmod(0o644)
    runtime = FakeRuntime()
    monkeypatch.setattr(config, "get_runtime", lambda _instance: runtime)
    monkeypatch.setattr(config, "validate_runtime_config", lambda *_args: None)
    monkeypatch.setattr(config, "preflight_enabled_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "reconcile_schedules", lambda *_args: None)
    monkeypatch.setattr(config, "generate_nginx_config", lambda *_args: None)
    monkeypatch.setattr(config, "generate_compose", lambda *_args: None)
    changed = previous.model_copy(deep=True)
    changed.ssl = config.SSLConfig(strategy="manual")

    config._apply_config_locked(
        changed,
        tmp_instance,
        manual_certificates=(cert_source, key_source),
    )

    assert (tmp_instance.certs_dir / "opal.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_instance.certs_dir / "opal.crt").stat().st_mode & 0o777 == 0o644


def test_automatic_updates_have_a_runtime_neutral_cli_alias(tmp_instance):
    save_config(OpalConfig(stack_name="study"), tmp_instance)

    result = CliRunner().invoke(
        config.config,
        ["auto-updates", "status"],
        obj={"instance": tmp_instance, "instances": [tmp_instance]},
    )

    assert result.exit_code == 0, result.output
    assert "Automatic updates" in result.output


def test_command_modules_do_not_invoke_docker_or_subprocess_directly():
    root = Path(__file__).parents[1]
    command_files = [
        "backup.py",
        "config.py",
        "diagnose.py",
        "doctor.py",
        "exec.py",
        "instances.py",
        "logs.py",
        "profiles.py",
        "support.py",
        "volumes.py",
    ]

    for filename in command_files:
        source = (root / "src" / "commands" / filename).read_text()
        assert not re.search(r"subprocess\.run\s*\(", source), filename
        assert not re.search(r"\[\s*['\"]docker['\"]", source), filename
