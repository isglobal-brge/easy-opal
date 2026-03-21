"""Test service modules produce correct compose fragments."""

import pytest
from src.models.config import OpalConfig, DatabaseConfig, ProfileConfig, WatchtowerConfig
from src.models.instance import InstanceContext
from src.services import ServiceRegistry


@pytest.fixture
def ctx(tmp_instance):
    return tmp_instance


@pytest.fixture
def secrets():
    return {
        "OPAL_ADMIN_PASSWORD": "testpass",
        "ROCK_ADMINISTRATOR_PASSWORD": "rockadmin",
        "ROCK_MANAGER_PASSWORD": "rockmgr",
        "ROCK_USER_PASSWORD": "rockusr",
        "ANALYTICS_PASSWORD": "dbpass",
        "DB1_PASSWORD": "db1pass",
    }


class TestServiceRegistry:
    def test_basic_compose(self, ctx, secrets):
        cfg = OpalConfig(stack_name="test")
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "mongo" in compose["services"]
        assert "opal" in compose["services"]
        assert "nginx" in compose["services"]
        assert "rock" in compose["services"]
        assert "certbot" not in compose["services"]  # only for letsencrypt

    def test_none_ssl_no_nginx(self, ctx, secrets):
        cfg = OpalConfig(ssl={"strategy": "none"})
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "nginx" not in compose["services"]
        assert "8080" in str(compose["services"]["opal"].get("ports", []))

    def test_letsencrypt_has_certbot(self, ctx, secrets):
        cfg = OpalConfig(ssl={"strategy": "letsencrypt", "le_email": "a@b.com"}, hosts=["x.com"])
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "certbot" in compose["services"]
        assert "80:80" in compose["services"]["nginx"]["ports"]

    def test_watchtower_when_enabled(self, ctx, secrets):
        cfg = OpalConfig(watchtower=WatchtowerConfig(enabled=True, poll_interval_hours=6))
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "watchtower" in compose["services"]
        assert compose["services"]["watchtower"]["environment"]["WATCHTOWER_POLL_INTERVAL"] == str(6 * 3600)

    def test_watchtower_when_disabled(self, ctx, secrets):
        cfg = OpalConfig(watchtower=WatchtowerConfig(enabled=False))
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "watchtower" not in compose["services"]

    def test_databases_added(self, ctx, secrets):
        cfg = OpalConfig(
            stack_name="myopal",
            databases=[DatabaseConfig(type="postgres", name="analytics", port=5432)],
        )
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "analytics" in compose["services"]
        assert "myopal-analytics-data" in compose["volumes"]

    def test_volume_naming_consistency(self, ctx, secrets):
        cfg = OpalConfig(
            stack_name="myopal",
            profiles=[ProfileConfig(name="rock"), ProfileConfig(name="rock-extra", image="datashield/rock-omics")],
            databases=[DatabaseConfig(type="postgres", name="db1", port=5432)],
        )
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        vols = list(compose["volumes"].keys())
        for v in vols:
            assert v.startswith("myopal-"), f"Volume {v} not prefixed"

    def test_csrf_wildcard(self, ctx, secrets):
        cfg = OpalConfig(hosts=["opal.dev"])
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        csrf = compose["services"]["opal"]["environment"]["CSRF_ALLOWED"]
        assert csrf == "*"

    def test_passwords(self, ctx, secrets):
        cfg = OpalConfig()
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        env = compose["services"]["opal"]["environment"]
        assert env["OPAL_ADMINISTRATOR_PASSWORD"] == "testpass"
        # Rock uses fixed "password" for Opal discovery compatibility
        assert env["ROCK_DEFAULT_ADMINISTRATOR_PASSWORD"] == "password"

    def test_healthchecks_present(self, ctx, secrets):
        cfg = OpalConfig(
            databases=[DatabaseConfig(type="postgres", name="db1", port=5432)],
        )
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        for svc_name in ["mongo", "opal", "rock", "db1"]:
            assert "healthcheck" in compose["services"][svc_name], f"No healthcheck on {svc_name}"

    def test_depends_on_service_healthy(self, ctx, secrets):
        cfg = OpalConfig()
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert compose["services"]["opal"]["depends_on"]["mongo"]["condition"] == "service_healthy"
        assert compose["services"]["rock"]["depends_on"]["opal"]["condition"] == "service_healthy"
        assert compose["services"]["nginx"]["depends_on"]["opal"]["condition"] == "service_healthy"
