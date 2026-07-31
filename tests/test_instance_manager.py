"""Registry / naming logic: instance name == stack name, config.json as source of truth."""

import json
import multiprocessing
import os
import time
from pathlib import Path

import pytest

from src.core import instance_manager as im


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate ~/.easy-opal to a temp dir for every test."""
    monkeypatch.setenv("EASY_OPAL_HOME", str(tmp_path))
    return tmp_path


def _write_config(root: Path, stack_name: str) -> None:
    (root / "config.json").write_text(
        json.dumps({"schema_version": 2, "stack_name": stack_name})
    )


def _hold_default_runtime_update(home: str, loaded, release) -> None:
    os.environ["EASY_OPAL_HOME"] = home
    with im._registry_lock():
        registry = im._load_registry()
        loaded.set()
        if not release.wait(5):
            raise RuntimeError("test did not release the registry writer")
        registry["default_runtime"] = "podman"
        im._save_registry(registry)


def _set_binding_concurrently(home: str, attempted, finished) -> None:
    os.environ["EASY_OPAL_HOME"] = home
    attempted.set()
    im.set_instance_runtime("study", "docker")
    finished.set()


class TestNaming:
    def test_create_instance_records_name_as_stack(self, home):
        ctx = im.create_instance("study1")
        assert ctx.name == "study1"
        assert im.get_registry_info()["study1"]["stack_name"] == "study1"

    def test_next_available_name_skips_instance_and_stack_names(self, home):
        im.create_instance("opal1")
        legacy = im.create_instance("legacy")
        _write_config(legacy.root, "opal2")  # divergent stack name
        im.update_stack_name("legacy", "opal2")
        # opal1 (instance) and opal2 (stack) are both taken -> opal3
        assert im.next_available_name("opal") == "opal3"

    def test_create_collides_with_existing_stack_name(self, home):
        legacy = im.create_instance("legacy")
        _write_config(legacy.root, "armfinal")
        im.update_stack_name("legacy", "armfinal")
        with pytest.raises(ValueError):
            im.create_instance("armfinal")

    def test_is_stack_name_taken_reads_config_json(self, home):
        legacy = im.create_instance("legacy")
        _write_config(legacy.root, "armfinal")  # registry mirror not updated
        assert im.is_stack_name_taken("armfinal") == "legacy"
        assert im.is_stack_name_taken("free") is None

    def test_is_stack_name_taken_discovers_unregistered_dir(self, home):
        # A dir copied/restored into instances/ but not yet registered.
        d = im.get_home() / "instances" / "restored"
        d.mkdir(parents=True)
        _write_config(d, "myhub")
        # Collision is detected even without a prior explicit sync.
        assert im.is_stack_name_taken("myhub") == "restored"
        with pytest.raises(ValueError):
            im.create_instance("myhub")


class TestSync:
    def test_sync_backfills_divergent_stack_name(self, home):
        ctx = im.create_instance("foo")
        _write_config(ctx.root, "bar")  # config disagrees with registry mirror
        reg = im.sync_registry()
        assert reg["instances"]["foo"]["stack_name"] == "bar"

    def test_sync_discovers_dir_and_reads_stack(self, home):
        d = im.get_home() / "instances" / "restored"
        d.mkdir(parents=True)
        _write_config(d, "restored-stack")  # simulate copied/restored dir
        reg = im.sync_registry()
        assert reg["instances"]["restored"]["stack_name"] == "restored-stack"

    def test_sync_drops_missing_dirs(self, home):
        im.create_instance("temp")
        import shutil
        shutil.rmtree(im.get_home() / "instances" / "temp")
        assert "temp" not in im.list_instances()

    def test_runtime_binding_is_host_local_registry_metadata(self, home):
        ctx = im.create_instance("bound")
        _write_config(ctx.root, "bound")
        im.set_instance_runtime(ctx, "podman")

        assert im.get_instance_runtime(ctx) == "podman"
        assert im.get_registry_info()["bound"]["runtime"] == "podman"
        assert "runtime" not in json.loads(ctx.config_path.read_text())

    def test_runtime_binding_rejects_unknown_runtime(self, home):
        ctx = im.create_instance("bound")
        with pytest.raises(ValueError, match="Unsupported container runtime"):
            im.set_instance_runtime(ctx, "containerd")

    def test_default_runtime_preference_is_host_local(self, home):
        ctx = im.create_instance("study")

        im.set_default_runtime("podman")

        assert im.get_default_runtime() == "podman"
        assert im._load_registry()["default_runtime"] == "podman"
        assert not ctx.config_path.exists()

    def test_selecting_auto_clears_default_runtime_preference(self, home):
        im.set_default_runtime("docker")

        im.set_default_runtime("auto")

        assert im.get_default_runtime() == "auto"
        assert "default_runtime" not in im._load_registry()

    def test_default_runtime_preference_rejects_unknown_runtime(self, home):
        with pytest.raises(ValueError, match="Unsupported default container runtime"):
            im.set_default_runtime("containerd")

    def test_default_and_binding_updates_are_cross_process_atomic(self, home):
        im.create_instance("study")
        context = multiprocessing.get_context("fork")
        loaded = context.Event()
        release = context.Event()
        attempted = context.Event()
        finished = context.Event()
        holder = context.Process(
            target=_hold_default_runtime_update,
            args=(str(home), loaded, release),
        )
        binder = context.Process(
            target=_set_binding_concurrently,
            args=(str(home), attempted, finished),
        )

        holder.start()
        try:
            assert loaded.wait(2)
            binder.start()
            assert attempted.wait(2)
            assert not finished.wait(0.1)
        finally:
            release.set()
            holder.join(5)
            if binder.pid is not None:
                binder.join(5)

        assert holder.exitcode == 0
        assert binder.exitcode == 0
        assert im.get_default_runtime() == "podman"
        assert im.get_instance_runtime("study") == "docker"


class TestLock:
    def test_wait_timeout_is_bounded_and_reports_expiry(self, home):
        ctx = im.create_instance("locked")

        with im.InstanceLock(ctx):
            started = time.monotonic()
            with pytest.raises(RuntimeError, match="remained locked"):
                with im.InstanceLock(ctx, timeout_seconds=0.02):
                    pass
            elapsed = time.monotonic() - started

        assert 0.015 <= elapsed < 1

    def test_negative_wait_timeout_is_rejected(self, home):
        ctx = im.create_instance("locked")

        with pytest.raises(ValueError, match="cannot be negative"):
            im.InstanceLock(ctx, timeout_seconds=-1)


class TestRemove:
    def test_remove_deletes_dir_and_is_not_rediscovered(self, home):
        ctx = im.create_instance("gone")
        im.remove_instance("gone", delete_data=False)
        assert "gone" not in im.get_registry_info()
        assert not ctx.root.exists()
        assert "gone" not in im.list_instances()  # sync must not bring it back

    def test_remove_delete_data_also_removes_dir(self, home):
        ctx = im.create_instance("gone2")
        im.remove_instance("gone2", delete_data=True)
        assert "gone2" not in im.get_registry_info()
        assert not ctx.root.exists()

    def test_remove_preserves_configured_instance_when_compose_is_missing(self, home):
        ctx = im.create_instance("incomplete")
        _write_config(ctx.root, "incomplete")
        data_file = ctx.data_dir / "important.txt"
        data_file.write_text("keep")

        with pytest.raises(RuntimeError, match="Compose file is missing"):
            im.remove_instance("incomplete", delete_data=True)

        assert ctx.config_path.exists()
        assert data_file.read_text() == "keep"
        assert "incomplete" in im.get_registry_info()

    def test_remove_missing_raises(self, home):
        with pytest.raises(ValueError):
            im.remove_instance("nope")

    def test_remove_uses_bound_podman_runtime(self, home, monkeypatch):
        from src.core import container_runtime as cr

        ctx = im.create_instance("podman-study")
        _write_config(ctx.root, "podman-stack")
        ctx.compose_path.write_text("services: {}\n")
        im.set_instance_runtime(ctx, "podman")
        monkeypatch.setattr(cr, "_requested_runtime", None)
        monkeypatch.delenv("EASY_OPAL_RUNTIME", raising=False)
        monkeypatch.setattr(
            cr.shutil,
            "which",
            lambda command: "/usr/bin/podman-compose"
            if command == "podman-compose"
            else None,
        )
        calls = []

        def fake_run(command, **kwargs):
            import subprocess

            calls.append(command)
            if command == ["/usr/bin/podman-compose", "version"]:
                return subprocess.CompletedProcess(
                    command, 0, "podman-compose version 1.6.0\n", ""
                )
            if command == ["podman", "--version"]:
                return subprocess.CompletedProcess(
                    command, 0, "podman version 4.6.0\n", ""
                )
            if command == [
                "podman", "info", "--format", "{{.Version.Version}}"
            ]:
                return subprocess.CompletedProcess(command, 0, "4.6.0\n", "")
            if command == ["podman", "compose", "up", "--help"]:
                return subprocess.CompletedProcess(
                    command, 0, "--wait --wait-timeout\n", ""
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(cr.subprocess, "run", fake_run)

        im.remove_instance("podman-study", delete_data=True)

        assert calls[-1] == [
            "podman",
            "compose",
            "--project-name",
            "podman-stack",
            "-f",
            str(ctx.compose_path),
            "down",
            "--remove-orphans",
            "-v",
        ]
        assert not ctx.root.exists()

    def test_remove_preserves_instance_when_compose_down_fails(self, home, monkeypatch):
        from src.core import container_runtime as cr

        ctx = im.create_instance("keep-me")
        _write_config(ctx.root, "keep-stack")
        ctx.compose_path.write_text("services: {}\n")
        data_file = ctx.data_dir / "important.txt"
        data_file.write_text("keep")
        im.set_instance_runtime(ctx, "podman")
        monkeypatch.setattr(cr, "_requested_runtime", None)
        monkeypatch.delenv("EASY_OPAL_RUNTIME", raising=False)
        monkeypatch.setattr(
            cr.shutil,
            "which",
            lambda command: "/usr/bin/podman-compose"
            if command == "podman-compose"
            else None,
        )

        def fake_run(command, **kwargs):
            import subprocess

            if command == ["/usr/bin/podman-compose", "version"]:
                return subprocess.CompletedProcess(
                    command, 0, "podman-compose version 1.6.0\n", ""
                )
            if command == ["podman", "--version"]:
                return subprocess.CompletedProcess(
                    command, 0, "podman version 4.6.0\n", ""
                )
            if command == [
                "podman", "info", "--format", "{{.Version.Version}}"
            ]:
                return subprocess.CompletedProcess(command, 0, "4.6.0\n", "")
            if command == ["podman", "compose", "up", "--help"]:
                return subprocess.CompletedProcess(
                    command, 0, "--wait --wait-timeout\n", ""
                )
            if "down" in command:
                return subprocess.CompletedProcess(command, 23, b"", b"teardown failed")
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(cr.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="data were preserved"):
            im.remove_instance("keep-me", delete_data=True)

        assert ctx.root.exists()
        assert ctx.compose_path.exists()
        assert data_file.read_text() == "keep"
        assert "keep-me" in im.get_registry_info()
