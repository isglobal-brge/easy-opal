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

    def test_failed_runtime_operation_returns_nonzero(
        self, home, runner, monkeypatch
    ):
        from src.commands import lifecycle
        from src.core.config_manager import save_config
        from src.models.config import OpalConfig

        instance = im.create_instance("study")
        save_config(OpalConfig(stack_name="study"), instance)
        monkeypatch.setattr(lifecycle, "compose_up", lambda *args: False)

        result = runner.invoke(main, ["-i", "study", "up"])

        assert result.exit_code != 0
        assert "Failed to start study" in result.output

    @pytest.mark.parametrize(
        "command",
        ["up", "down", "restart", "status", "plan", "validate", "reset"],
    )
    def test_lifecycle_commands_fail_when_configuration_is_missing(
        self, home, runner, command
    ):
        im.create_instance("empty")

        result = runner.invoke(main, ["-i", "empty", command])

        assert result.exit_code != 0
        assert "No configuration found" in result.output

    def test_validate_returns_nonzero_when_configuration_has_issues(
        self, home, runner, monkeypatch
    ):
        from src.core.config_manager import save_config
        from src.models.config import OpalConfig

        instance = im.create_instance("invalid")
        save_config(
            OpalConfig(
                stack_name="invalid",
                ssl={"strategy": "letsencrypt", "le_email": ""},
            ),
            instance,
        )
        monkeypatch.setattr(
            "src.core.docker.generate_compose", lambda *args, **kwargs: None
        )

        result = runner.invoke(main, ["-i", "invalid", "validate"])

        assert result.exit_code != 0
        assert "Let's Encrypt email not set" in result.output
        assert "Configuration is invalid" in result.output


class TestStackAlias:
    def test_stack_alias_lists_instances(self, home, runner):
        im.create_instance("a")
        result = runner.invoke(main, ["stack", "list"])
        assert result.exit_code == 0
        assert "a" in result.output

    def test_instance_list_does_not_bind_legacy_instance(self, home, runner):
        from src.core.config_manager import save_config
        from src.models.config import OpalConfig

        instance = im.create_instance("legacy")
        save_config(OpalConfig(stack_name="legacy"), instance)

        result = runner.invoke(main, ["instance", "list"])

        assert result.exit_code == 0, result.output
        assert "unbound" in result.output
        assert im.get_instance_runtime(instance) is None

    def test_instance_list_survives_an_unavailable_bound_runtime(
        self, home, runner, monkeypatch
    ):
        from src.commands import instances as instances_mod
        from src.core.config_manager import save_config
        from src.core.container_runtime import RuntimeSelectionError
        from src.models.config import OpalConfig

        instance = im.create_instance("podman-study")
        im.set_instance_runtime(instance, "podman")
        save_config(OpalConfig(stack_name="podman-study"), instance)

        def unavailable(_instance):
            raise RuntimeSelectionError("Podman machine is stopped")

        monkeypatch.setattr(instances_mod, "get_runtime", unavailable)

        result = runner.invoke(main, ["instance", "list"])

        assert result.exit_code == 0, result.output
        assert "unavail" in result.output


@pytest.fixture
def no_docker(monkeypatch):
    """Stub out runtime touchpoints so setup can run without a real stack."""
    import src.commands.setup as setup_mod
    runtime = type("Runtime", (), {"name": "docker"})()
    monkeypatch.setattr(setup_mod, "get_runtime", lambda instance=None: runtime)
    monkeypatch.setattr(
        setup_mod, "rootless_port_threshold", lambda runtime: None
    )
    monkeypatch.setattr(setup_mod, "compose_up", lambda *a, **k: True)
    monkeypatch.setattr(setup_mod, "generate_compose", lambda *a, **k: None)
    monkeypatch.setattr(setup_mod, "generate_nginx_config", lambda *a, **k: None)
    monkeypatch.setattr(
        setup_mod, "preflight_enabled_schedules", lambda *a, **k: None
    )
    monkeypatch.setattr(setup_mod, "reconcile_schedules", lambda *a, **k: None)


class TestRuntimeSelection:
    def test_runtime_option_is_forwarded(self, home, runner, monkeypatch):
        selected = []
        monkeypatch.setattr("src.cli.set_requested_runtime", selected.append)

        result = runner.invoke(main, ["--runtime", "podman", "instance", "list"])

        assert result.exit_code == 0, result.output
        assert selected == ["podman"]

    def test_runtime_environment_variable_is_forwarded(
        self, home, runner, monkeypatch
    ):
        selected = []
        monkeypatch.setenv("EASY_OPAL_RUNTIME", "docker")
        monkeypatch.setattr("src.cli.set_requested_runtime", selected.append)

        result = runner.invoke(main, ["instance", "list"])

        assert result.exit_code == 0, result.output
        assert selected == ["docker"]

    def test_explicit_runtime_binds_new_instance(
        self, home, runner, monkeypatch
    ):
        import src.commands.instances as instances_mod

        runtime = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(instances_mod, "get_runtime", lambda: runtime)

        result = runner.invoke(
            main, ["--runtime", "podman", "instance", "create", "study"]
        )

        assert result.exit_code == 0, result.output
        assert im.get_instance_runtime("study") == "podman"


class TestScheduledCommands:
    def test_profile_pull_skips_disabled_feature_without_runtime(
        self, home, runner, monkeypatch
    ):
        import src.commands.profiles as profiles_mod
        from src.core.config_manager import save_config
        from src.models.config import OpalConfig

        instance = im.create_instance("study")
        save_config(
            OpalConfig(profile_updater={"enabled": False}),
            instance,
        )
        config_before = instance.config_path.read_bytes()

        def unexpected_runtime(_instance):
            raise AssertionError(
                "disabled scheduled profile pull must not resolve a runtime"
            )

        monkeypatch.setattr(profiles_mod, "get_runtime", unexpected_runtime)

        result = runner.invoke(
            main,
            ["-i", "study", "profile", "pull", "--scheduled"],
        )

        assert result.exit_code == 0, result.output
        assert "Scheduled profile pulls are disabled; skipping." in result.output
        assert instance.config_path.read_bytes() == config_before


class TestSetupNaming:
    def test_backup_collection_reprompts_for_negative_retention(
        self, monkeypatch
    ):
        import src.commands.setup as setup_mod
        from src.models.config import OpalConfig

        confirmations = iter([True, True])
        answers = iter([24, -1, 3])
        monkeypatch.setattr(
            setup_mod.Confirm,
            "ask",
            lambda *args, **kwargs: next(confirmations),
        )
        monkeypatch.setattr(
            setup_mod.IntPrompt,
            "ask",
            lambda *args, **kwargs: next(answers),
        )

        cfg = setup_mod._collect_backup(OpalConfig(), "podman")

        assert cfg.backup.enabled is True
        assert cfg.backup.interval_hours == 24
        assert cfg.backup.keep == 3

    def test_setup_without_start_still_materializes_compose(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod
        from src.models import SSLConfig, SSLStrategy

        monkeypatch.setattr(setup_mod, "_collect_general", lambda cfg: cfg)

        def collect_ssl(cfg):
            cfg.ssl = SSLConfig(strategy=SSLStrategy.NONE)
            cfg.hosts = []
            return cfg

        monkeypatch.setattr(setup_mod, "_collect_ssl", collect_ssl)
        monkeypatch.setattr(setup_mod, "_collect_databases", lambda cfg: cfg)
        monkeypatch.setattr(
            setup_mod, "_collect_watchtower", lambda cfg, _runtime: cfg
        )
        monkeypatch.setattr(
            setup_mod, "_collect_backup", lambda cfg, _runtime: cfg
        )
        monkeypatch.setattr(setup_mod, "_collect_optional_services", lambda cfg: cfg)
        monkeypatch.setattr(setup_mod.Confirm, "ask", lambda *args, **kwargs: False)

        generated = []

        def generate(_cfg, instance):
            generated.append(instance.name)
            instance.compose_path.write_text("services: {}\n")

        monkeypatch.setattr(setup_mod, "generate_compose", generate)
        monkeypatch.setattr(
            setup_mod,
            "compose_up",
            lambda *args, **kwargs: pytest.fail("stack must not be started"),
        )

        result = runner.invoke(main, ["setup", "--name", "planned"])

        assert result.exit_code == 0, result.output
        instance = im.get_instance("planned")
        assert generated == ["planned"]
        assert instance.config_path.is_file()
        assert instance.compose_path.read_text() == "services: {}\n"

    def test_invalid_manual_certificate_does_not_persist_or_schedule(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod

        events = []
        monkeypatch.setattr(
            setup_mod,
            "preflight_enabled_schedules",
            lambda *args: events.append("preflight"),
        )
        monkeypatch.setattr(
            setup_mod,
            "reconcile_schedules",
            lambda *args: events.append("reconcile"),
        )
        monkeypatch.setattr(
            setup_mod,
            "generate_compose",
            lambda *args: events.append("compose"),
        )

        result = runner.invoke(
            main,
            [
                "setup",
                "--name",
                "bad-cert",
                "--yes",
                "--ssl-strategy",
                "manual",
                "--ssl-cert",
                str(home / "missing.crt"),
                "--ssl-key",
                str(home / "missing.key"),
            ],
        )

        assert result.exit_code == 1
        assert "Certificate or key file not found" in result.output
        instance = im.get_instance("bad-cert")
        assert events == []
        assert not instance.config_path.exists()
        assert not instance.secrets_path.exists()
        assert not instance.compose_path.exists()
        assert not (instance.nginx_conf_dir / "nginx.conf").exists()

    def test_generation_failure_removes_new_setup_artifacts(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod

        reconciled = []

        def fail_compose(_cfg, instance):
            instance.compose_path.write_text("partial compose\n")
            raise OSError("simulated compose failure")

        monkeypatch.setattr(setup_mod, "generate_compose", fail_compose)
        monkeypatch.setattr(
            setup_mod,
            "reconcile_schedules",
            lambda *args: reconciled.append(True),
        )

        result = runner.invoke(
            main,
            [
                "setup",
                "--name",
                "generation-failure",
                "--yes",
                "--ssl-strategy",
                "none",
            ],
        )

        assert result.exit_code == 1
        assert "previous files and schedules restored" in result.output
        instance = im.get_instance("generation-failure")
        assert reconciled == []
        assert not instance.config_path.exists()
        assert not instance.secrets_path.exists()
        assert not instance.compose_path.exists()

    def test_scheduler_failure_rolls_back_new_setup_artifacts_and_schedule(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod

        monkeypatch.setattr(
            setup_mod,
            "generate_compose",
            lambda _cfg, instance: instance.compose_path.write_text("new compose\n"),
        )
        reconciled = []

        def reconcile(_instance, _runtime, cfg):
            reconciled.append(cfg.watchtower.enabled)
            if cfg.watchtower.enabled:
                raise setup_mod.AutoUpdateScheduleError(
                    "simulated scheduler failure"
                )

        monkeypatch.setattr(setup_mod, "reconcile_schedules", reconcile)

        result = runner.invoke(
            main,
            [
                "setup",
                "--name",
                "scheduler-failure",
                "--yes",
                "--ssl-strategy",
                "none",
                "--auto-updates",
            ],
        )

        assert result.exit_code == 1
        assert "previous files and schedules restored" in result.output
        instance = im.get_instance("scheduler-failure")
        assert reconciled == [True, False]
        assert not instance.config_path.exists()
        assert not instance.secrets_path.exists()
        assert not instance.compose_path.exists()

    def test_setup_accepts_runtime_neutral_auto_update_flags(
        self, home, runner, no_docker
    ):
        from src.core.config_manager import load_config

        result = runner.invoke(
            main,
            [
                "setup",
                "--name",
                "neutral-flags",
                "--yes",
                "--ssl-strategy",
                "none",
                "--auto-updates",
                "--auto-update-interval",
                "7",
            ],
        )

        assert result.exit_code == 0, result.output
        cfg = load_config(im.get_instance("neutral-flags"))
        assert cfg.watchtower.enabled is True
        assert cfg.watchtower.poll_interval_hours == 7

    def test_setup_help_lists_neutral_and_legacy_auto_update_flags(
        self, home, runner
    ):
        result = runner.invoke(main, ["setup", "--help"])

        assert result.exit_code == 0, result.output
        for flag in (
            "--auto-updates",
            "--no-auto-updates",
            "--auto-update-interval",
            "--watchtower",
            "--no-watchtower",
            "--watchtower-interval",
        ):
            assert flag in result.output

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

    def test_setup_by_name_preserves_existing_runtime_binding(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod

        instance = im.create_instance("existing")
        im.set_instance_runtime(instance, "podman")
        docker = type("Runtime", (), {"name": "docker"})()
        podman = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(
            setup_mod,
            "get_runtime",
            lambda target=None: podman if target is not None else docker,
        )

        result = runner.invoke(
            main,
            ["setup", "--name", "existing", "--yes", "--ssl-strategy", "none"],
        )

        assert result.exit_code == 0, result.output
        assert im.get_instance_runtime(instance) == "podman"

    def test_setup_revalidates_runtime_after_acquiring_instance_lock(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod

        instance = im.create_instance("existing")
        runtime = type("Runtime", (), {"name": "podman"})()
        state = {"locked": False, "runtime_calls": 0}

        class TrackingLock:
            def __init__(self, target):
                assert target == instance

            def __enter__(self):
                state["locked"] = True
                return self

            def __exit__(self, *exc_info):
                state["locked"] = False

        def get_runtime(target=None):
            state["runtime_calls"] += 1
            if state["runtime_calls"] == 2:
                assert state["locked"] is True
            return runtime

        monkeypatch.setattr(setup_mod, "InstanceLock", TrackingLock)
        monkeypatch.setattr(setup_mod, "get_runtime", get_runtime)

        result = runner.invoke(
            main,
            ["setup", "--name", "existing", "--yes", "--ssl-strategy", "none"],
        )

        assert result.exit_code == 0, result.output
        assert state["runtime_calls"] == 2

    def test_existing_podman_binding_is_resolved_before_preset(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod
        from src.core.config_manager import load_config

        instance = im.create_instance("existing")
        im.set_instance_runtime(instance, "podman")
        docker = type("Runtime", (), {"name": "docker"})()
        podman = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(
            setup_mod,
            "get_runtime",
            lambda target=None: podman if target is not None else docker,
        )

        result = runner.invoke(
            main,
            [
                "setup",
                "--name",
                "existing",
                "--preset",
                "opal-prod",
                "--ssl-strategy",
                "none",
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output
        assert load_config(instance).watchtower.enabled is False

    def test_podman_supports_automatic_updates_without_docker_socket(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod
        from src.core.config_manager import load_config

        podman = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(setup_mod, "get_runtime", lambda target=None: podman)

        result = runner.invoke(
            main,
            [
                "--runtime",
                "podman",
                "setup",
                "--name",
                "podman-study",
                "--yes",
                "--ssl-strategy",
                "none",
                "--watchtower",
            ],
        )

        assert result.exit_code == 0, result.output
        assert load_config(im.get_instance("podman-study")).watchtower.enabled is True

    def test_armadillo_setup_rejects_additional_database_services(
        self, home, runner, no_docker
    ):
        result = runner.invoke(
            main,
            [
                "setup",
                "--name",
                "armadillo-db",
                "--flavor",
                "armadillo",
                "--database",
                "postgres:analytics:5432:opal",
                "--ssl-strategy",
                "none",
                "--yes",
            ],
        )

        assert result.exit_code == 1
        assert "only supported for the Opal flavor" in result.output
        assert not im.get_instance("armadillo-db").config_path.exists()

    def test_new_rootless_podman_instance_uses_unprivileged_https_port(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod
        from src.core.config_manager import load_config

        podman = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(setup_mod, "get_runtime", lambda target=None: podman)
        monkeypatch.setattr(
            setup_mod, "rootless_port_threshold", lambda runtime: 1024
        )

        result = runner.invoke(
            main,
            ["--runtime", "podman", "setup", "--name", "rootless", "--yes"],
        )

        assert result.exit_code == 0, result.output
        assert load_config(im.get_instance("rootless")).opal_external_port == 8443

    def test_rootless_podman_rejects_explicit_privileged_port_cleanly(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod

        podman = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(setup_mod, "get_runtime", lambda target=None: podman)
        monkeypatch.setattr(
            setup_mod, "rootless_port_threshold", lambda runtime: 1024
        )

        result = runner.invoke(
            main,
            [
                "--runtime",
                "podman",
                "setup",
                "--name",
                "rootless-low",
                "--port",
                "443",
                "--yes",
            ],
        )

        assert result.exit_code == 1
        assert "Rootless Podman cannot publish host port(s) 443" in result.output
        assert not im.get_instance("rootless-low").config_path.exists()

    def test_production_preset_keeps_automatic_updates_opt_in(
        self, home, runner, no_docker, monkeypatch
    ):
        import src.commands.setup as setup_mod
        from src.core.config_manager import load_config

        podman = type("Runtime", (), {"name": "podman"})()
        monkeypatch.setattr(setup_mod, "get_runtime", lambda target=None: podman)

        result = runner.invoke(
            main,
            [
                "--runtime",
                "podman",
                "setup",
                "--name",
                "production",
                "--preset",
                "opal-prod",
                "--ssl-strategy",
                "none",
                "--yes",
            ],
        )

        assert result.exit_code == 0, result.output
        assert load_config(im.get_instance("production")).watchtower.enabled is False
