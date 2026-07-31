"""Test service modules produce correct compose fragments."""

import pytest
from src.models.config import (
    DatabaseConfig,
    OpalConfig,
    ProfileConfig,
)
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
        assert compose["services"]["certbot"]["profiles"] == ["certbot"]
        assert "80:80" in compose["services"]["nginx"]["ports"]

    def test_automatic_updates_do_not_add_a_socket_sidecar(self, ctx, secrets):
        cfg = OpalConfig(watchtower={"enabled": True, "poll_interval_hours": 6})
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert "watchtower" not in compose["services"]
        assert "/var/run/docker.sock" not in repr(compose)

    def test_watchtower_when_disabled(self, ctx, secrets):
        cfg = OpalConfig(watchtower={"enabled": False})
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

    def test_armadillo_rejects_additional_database_services(self, ctx, secrets):
        cfg = OpalConfig(flavor="armadillo")
        cfg.databases.append(
            DatabaseConfig(type="postgres", name="analytics", port=5432)
        )

        with pytest.raises(ValueError, match="only supported for the Opal flavor"):
            ServiceRegistry(cfg, ctx, secrets).assemble_compose()

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

    def test_fixed_images_are_fully_qualified(self, ctx, secrets):
        cfg = OpalConfig(
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
            agate={"enabled": True, "mail_mode": "mailpit"},
            mica={"enabled": True},
            databases=[
                DatabaseConfig(type="postgres", name="pg", port=5432),
                DatabaseConfig(type="mysql", name="mysql", port=3306),
                DatabaseConfig(type="mariadb", name="maria", port=3307),
            ],
        )
        database_secrets = {
            **secrets,
            "PG_PASSWORD": "pgpass",
            "MYSQL_PASSWORD": "mysqlpass",
            "MARIA_PASSWORD": "mariapass",
        }
        compose = ServiceRegistry(cfg, ctx, database_secrets).assemble_compose()

        expected = {
            "mongo": "docker.io/library/mongo:latest",
            "opal": "docker.io/obiba/opal:latest",
            "rock": "docker.io/datashield/rock-base:latest",
            "nginx": "docker.io/library/nginx:latest",
            "certbot": "docker.io/certbot/certbot",
            "agate": "docker.io/obiba/agate:latest",
            "mailpit": "docker.io/axllent/mailpit:latest",
            "mica": "docker.io/obiba/mica:latest",
            "elasticsearch": "docker.elastic.co/elasticsearch/elasticsearch:8.16.1",
            "pg": "docker.io/library/postgres:latest",
            "mysql": "docker.io/library/mysql:latest",
            "maria": "docker.io/library/mariadb:latest",
        }
        for service_name, image in expected.items():
            assert compose["services"][service_name]["image"] == image

        armadillo = ServiceRegistry(
            OpalConfig(flavor="armadillo", keycloak={"enabled": True}),
            ctx,
            secrets,
        ).assemble_compose()
        assert (
            armadillo["services"]["armadillo"]["image"]
            == "docker.io/molgenis/molgenis-armadillo:latest"
        )
        assert (
            armadillo["services"]["keycloak"]["image"]
            == "quay.io/keycloak/keycloak:25.0.6"
        )

    def test_profile_image_keeps_explicit_registry(self, ctx, secrets):
        cfg = OpalConfig(
            profiles=[
                ProfileConfig(name="rock", image="registry.example.org/ds/rock")
            ]
        )

        compose = ServiceRegistry(cfg, ctx, secrets).assemble_compose()

        assert (
            compose["services"]["rock"]["image"]
            == "registry.example.org/ds/rock:latest"
        )

    def test_bind_mounts_have_selinux_relabel_modes(self, ctx, secrets):
        cfg = OpalConfig(
            ssl={"strategy": "letsencrypt", "le_email": "admin@example.org"},
            agate={"enabled": True},
        )
        compose = ServiceRegistry(cfg, ctx, secrets).assemble_compose()

        nginx_mounts = compose["services"]["nginx"]["volumes"]
        assert any(m.endswith(":/etc/nginx/nginx.conf:ro,Z") for m in nginx_mounts)
        assert any(m.endswith(":/etc/nginx/certs:ro,Z") for m in nginx_mounts)
        assert any(m.endswith(":/usr/share/nginx/html:ro,Z") for m in nginx_mounts)
        assert any(m.endswith(":/var/www/certbot:ro,z") for m in nginx_mounts)
        assert any(m.endswith(":/etc/letsencrypt:ro,z") for m in nginx_mounts)

        certbot_mounts = compose["services"]["certbot"]["volumes"]
        assert any(m.endswith(":/var/www/certbot:rw,z") for m in certbot_mounts)
        assert any(m.endswith(":/etc/letsencrypt:rw,z") for m in certbot_mounts)
        assert compose["services"]["agate"]["volumes"][0].endswith(":/srv:Z")

        armadillo = ServiceRegistry(
            OpalConfig(flavor="armadillo"), ctx, secrets
        ).assemble_compose()
        assert any(
            mount.endswith(":/config:Z")
            for mount in armadillo["services"]["armadillo"]["volumes"]
        )

    def test_depends_on_service_healthy(self, ctx, secrets):
        cfg = OpalConfig()
        reg = ServiceRegistry(cfg, ctx, secrets)
        compose = reg.assemble_compose()

        assert compose["services"]["opal"]["depends_on"]["mongo"]["condition"] == "service_healthy"
        assert compose["services"]["rock"]["depends_on"]["opal"]["condition"] == "service_healthy"
        assert compose["services"]["nginx"]["depends_on"]["opal"]["condition"] == "service_healthy"

    def test_podman_compose_has_no_docker_dependencies(self, ctx, secrets):
        compose = ServiceRegistry(
            OpalConfig(), ctx, secrets, runtime_name="podman"
        ).assemble_compose()

        rendered = repr(compose)
        assert "docker:cli" not in rendered
        assert "docker exec" not in rendered
        assert "docker pull" not in rendered
        assert "/var/run/docker.sock" not in rendered

    @pytest.mark.parametrize("runtime_name", ["docker", "podman"])
    def test_host_jobs_add_no_engine_cli_or_socket_sidecars(
        self, ctx, secrets, runtime_name
    ):
        cfg = OpalConfig(
            watchtower={"enabled": True},
            backup={"enabled": True},
            profile_updater={"enabled": True},
        )
        compose = ServiceRegistry(
            cfg, ctx, secrets, runtime_name=runtime_name
        ).assemble_compose()

        rendered = repr(compose)
        assert "watchtower" not in compose["services"]
        assert "backup" not in compose["services"]
        assert "profile-updater" not in compose["services"]
        assert "docker:cli" not in rendered
        assert "/var/run/docker.sock" not in rendered
        assert not (ctx.data_dir / "backup-script").exists()
        assert not (ctx.data_dir / "profile-updater-script").exists()

    def test_unknown_runtime_is_rejected(self, ctx, secrets):
        with pytest.raises(ValueError, match="Unsupported container runtime 'containerd'"):
            ServiceRegistry(OpalConfig(), ctx, secrets, runtime_name="containerd")
