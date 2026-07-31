"""CLI selection of the default Docker or Podman runtime."""

import pytest
from click.testing import CliRunner

from src.cli import main
from src.core import container_runtime as cr
from src.core import instance_manager as im


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_OPAL_HOME", str(tmp_path))
    monkeypatch.delenv("EASY_OPAL_RUNTIME", raising=False)
    monkeypatch.setattr(cr, "_requested_runtime", None)


@pytest.fixture
def runner():
    return CliRunner()


def _runtime(name):
    return cr.ContainerRuntime(name, name, (name, "compose"))


def test_runtime_status_is_global_and_read_only(runner, monkeypatch):
    first = im.create_instance("first")
    second = im.create_instance("second")
    im.set_instance_runtime(first, "docker")
    im.set_instance_runtime(second, "podman")
    before = im._registry_path().read_bytes()

    monkeypatch.setattr(
        "src.commands.runtime.probe_runtimes",
        lambda: (
            {"docker": _runtime("docker")},
            {"podman": "engine service unavailable"},
        ),
    )

    result = runner.invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "auto" in result.output
    assert "Docker" in result.output
    assert "Podman" in result.output
    assert "engine service unavailable" in result.output
    assert im._registry_path().read_bytes() == before


def test_runtime_status_shows_environment_override(runner, monkeypatch):
    im.set_default_runtime("docker")
    monkeypatch.setenv("EASY_OPAL_RUNTIME", "podman")
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtimes",
        lambda: (
            {"docker": _runtime("docker"), "podman": _runtime("podman")},
            {},
        ),
    )

    result = runner.invoke(main, ["runtime", "status"])

    assert result.exit_code == 0, result.output
    assert "Default runtime for new setups: docker" in result.output
    assert "Active CLI/environment override: podman" in result.output


@pytest.mark.parametrize("name", ["docker", "podman"])
def test_runtime_select_with_argument_is_non_interactive(
    runner, monkeypatch, name
):
    calls = []

    def probe(selected):
        calls.append(selected)
        return _runtime(selected), ""

    monkeypatch.setattr("src.commands.runtime.probe_runtime", probe)

    result = runner.invoke(main, ["runtime", "select", name])

    assert result.exit_code == 0, result.output
    assert calls == [name]
    assert im.get_default_runtime() == name
    assert "Existing instance bindings were not changed" in result.output


def test_runtime_select_does_not_fallback_or_mutate_on_failure(
    runner, monkeypatch
):
    im.set_default_runtime("docker")
    calls = []

    def probe(selected):
        calls.append(selected)
        return None, "podman service is stopped"

    monkeypatch.setattr("src.commands.runtime.probe_runtime", probe)

    result = runner.invoke(main, ["runtime", "select", "podman"])

    assert result.exit_code == 1
    assert "podman service is stopped" in result.output
    assert calls == ["podman"]
    assert im.get_default_runtime() == "docker"


def test_runtime_select_auto_clears_without_probing(runner, monkeypatch):
    im.set_default_runtime("podman")
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtime",
        lambda _name: pytest.fail("auto must not probe an engine"),
    )

    result = runner.invoke(main, ["runtime", "select", "auto"])

    assert result.exit_code == 0, result.output
    assert im.get_default_runtime() == "auto"


def test_runtime_select_without_argument_is_an_interactive_wizard(
    runner, monkeypatch
):
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtimes",
        lambda: (
            {"docker": _runtime("docker"), "podman": _runtime("podman")},
            {},
        ),
    )

    result = runner.invoke(main, ["runtime", "select"], input="podman\n")

    assert result.exit_code == 0, result.output
    assert "Default runtime" in result.output
    assert im.get_default_runtime() == "podman"


def test_runtime_select_wizard_keeps_auto_when_accepting_default(
    runner, monkeypatch
):
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtimes",
        lambda: (
            {"docker": _runtime("docker"), "podman": _runtime("podman")},
            {},
        ),
    )

    result = runner.invoke(main, ["runtime", "select"], input="\n")

    assert result.exit_code == 0, result.output
    assert im.get_default_runtime() == "auto"


def test_runtime_select_wizard_can_clear_to_auto_when_no_engine_is_usable(
    runner, monkeypatch
):
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtimes",
        lambda: (
            {},
            {"docker": "daemon stopped", "podman": "machine stopped"},
        ),
    )

    result = runner.invoke(main, ["runtime", "select"], input="\n")

    assert result.exit_code == 0, result.output
    assert "only automatic detection can be selected" in result.output
    assert "daemon stopped" in result.output
    assert "machine stopped" in result.output
    assert im.get_default_runtime() == "auto"


def test_runtime_select_warns_when_environment_still_overrides_saved_default(
    runner, monkeypatch
):
    monkeypatch.setenv("EASY_OPAL_RUNTIME", "docker")
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtime",
        lambda name: (_runtime(name), ""),
    )

    result = runner.invoke(main, ["runtime", "select", "podman"])

    assert result.exit_code == 0, result.output
    assert im.get_default_runtime() == "podman"
    assert "override remains 'docker'" in result.output


def test_changing_default_does_not_rebind_existing_instances(
    runner, monkeypatch
):
    instance = im.create_instance("study")
    im.set_instance_runtime(instance, "docker")
    monkeypatch.setattr(
        "src.commands.runtime.probe_runtime",
        lambda name: (_runtime(name), ""),
    )

    result = runner.invoke(main, ["runtime", "select", "podman"])

    assert result.exit_code == 0, result.output
    assert im.get_default_runtime() == "podman"
    assert im.get_instance_runtime(instance) == "docker"
