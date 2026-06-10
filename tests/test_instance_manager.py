"""Registry / naming logic: instance name == stack name, config.json as source of truth."""

import json
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


class TestRemove:
    def test_remove_deletes_dir_and_is_not_rediscovered(self, home):
        ctx = im.create_instance("gone")
        _write_config(ctx.root, "gone")  # no compose file -> no Docker needed
        im.remove_instance("gone", delete_data=False)
        assert "gone" not in im.get_registry_info()
        assert not ctx.root.exists()
        assert "gone" not in im.list_instances()  # sync must not bring it back

    def test_remove_delete_data_also_removes_dir(self, home):
        ctx = im.create_instance("gone2")
        _write_config(ctx.root, "gone2")
        im.remove_instance("gone2", delete_data=True)
        assert "gone2" not in im.get_registry_info()
        assert not ctx.root.exists()

    def test_remove_missing_raises(self, home):
        with pytest.raises(ValueError):
            im.remove_instance("nope")
