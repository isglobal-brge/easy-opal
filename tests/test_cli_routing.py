"""CLI routing: global vs instance-scoped commands, -i targeting, multi-target guards."""

import pytest
from click.testing import CliRunner

from src.cli import main
from src.core import instance_manager as im


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_OPAL_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def runner():
    return CliRunner()


class TestGlobalCommands:
    def test_update_does_not_require_instance(self, home, runner):
        # Multiple instances would make the old code demand -i.
        im.create_instance("a")
        im.create_instance("b")
        result = runner.invoke(main, ["update", "--help"])
        assert result.exit_code == 0
        assert "Update easy-opal" in result.output

    def test_doctor_runs_global_with_many_instances(self, home, runner):
        im.create_instance("a")
        im.create_instance("b")
        result = runner.invoke(main, ["doctor"])
        assert "Multiple instances" not in result.output
        assert "Registry" in result.output


class TestInstanceTargeting:
    def test_empty_comma_list_clean_error(self, home, runner):
        im.create_instance("a")
        result = runner.invoke(main, ["-i", ",,", "status"])
        assert result.exit_code != 0
        assert "No valid instance names" in result.output

    def test_single_target_command_rejects_all(self, home, runner):
        im.create_instance("a")
        im.create_instance("b")
        result = runner.invoke(main, ["--all", "config", "show"])
        assert result.exit_code != 0
        assert "single instance" in result.output

    def test_unknown_instance_errors(self, home, runner):
        im.create_instance("a")
        result = runner.invoke(main, ["-i", "ghost", "status"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestStackAlias:
    def test_stack_alias_lists_instances(self, home, runner):
        im.create_instance("a")
        result = runner.invoke(main, ["stack", "list"])
        assert result.exit_code == 0
        assert "a" in result.output


@pytest.fixture
def no_docker(monkeypatch):
    """Stub out Docker touchpoints so setup can run without a real stack."""
    import src.commands.setup as setup_mod
    monkeypatch.setattr(setup_mod, "check_docker", lambda: True)
    monkeypatch.setattr(setup_mod, "compose_up", lambda *a, **k: True)
    monkeypatch.setattr(setup_mod, "generate_nginx_config", lambda *a, **k: None)


class TestSetupNaming:
    def test_name_flag_becomes_instance_and_stack(self, home, runner, no_docker):
        from src.core.config_manager import load_config

        result = runner.invoke(main, [
            "setup", "--name", "mystudy", "--yes",
            "--ssl-strategy", "none", "--http-port", "18080",
        ])
        assert result.exit_code == 0, result.output
        assert "mystudy" in im.get_registry_info()
        cfg = load_config(im.get_instance("mystudy"))
        assert cfg.stack_name == "mystudy"  # name == stack

    def test_stack_name_alias_creates_named_instance(self, home, runner, no_docker):
        result = runner.invoke(main, [
            "setup", "--stack-name", "aphrc", "--yes", "--ssl-strategy", "none",
        ])
        assert result.exit_code == 0, result.output
        assert "aphrc" in im.get_registry_info()

    def test_setup_auto_names_when_no_name_given(self, home, runner, no_docker):
        result = runner.invoke(main, ["setup", "--yes", "--ssl-strategy", "none"])
        assert result.exit_code == 0, result.output
        assert "opal1" in im.get_registry_info()

    def test_setup_targets_existing_instance_by_name(self, home, runner, no_docker):
        im.create_instance("existing")
        result = runner.invoke(main, [
            "-i", "existing", "setup", "--yes", "--ssl-strategy", "none",
        ])
        assert result.exit_code == 0, result.output
        # No duplicate auto-named instance was created.
        names = set(im.get_registry_info())
        assert names == {"existing"}
