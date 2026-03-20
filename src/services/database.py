"""Additional database services: PostgreSQL, MySQL, MariaDB."""
from src.models.config import DatabaseConfig, OpalConfig
from src.models.enums import DatabaseType
from src.models.instance import InstanceContext


class DatabaseService:
    def __init__(self, db: DatabaseConfig):
        self.db = db
        self.name = f"db-{db.name}"

    def is_enabled(self, config: OpalConfig) -> bool:
        return True

    def compose_services(
        self, config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]
    ) -> dict:
        db = self.db
        container = f"{config.stack_name}-{db.name}"
        volume = f"{config.stack_name}-{db.name}-data"
        pw_key = f"{db.name.upper().replace('-', '_')}_PASSWORD"
        password = secrets[pw_key]

        if db.type == DatabaseType.POSTGRES:
            return {
                db.name: {
                    "image": f"postgres:{db.version}",
                    "container_name": container,
                    "restart": "always",
                    "environment": {
                        "POSTGRES_USER": db.user,
                        "POSTGRES_PASSWORD": password,
                        "POSTGRES_DB": db.database,
                    },
                    "volumes": [f"{volume}:/var/lib/postgresql/data"],
                    "ports": [f"{db.port}:5432"],
                    "healthcheck": {
                        "test": ["CMD-SHELL", f"pg_isready -U {db.user}"],
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 5,
                    },
                }
            }

        elif db.type == DatabaseType.MYSQL:
            return {
                db.name: {
                    "image": f"mysql:{db.version}",
                    "container_name": container,
                    "restart": "always",
                    "environment": {
                        "MYSQL_ROOT_PASSWORD": password,
                        "MYSQL_DATABASE": db.database,
                        "MYSQL_USER": db.user,
                        "MYSQL_PASSWORD": password,
                    },
                    "volumes": [f"{volume}:/var/lib/mysql"],
                    "ports": [f"{db.port}:3306"],
                    "healthcheck": {
                        "test": ["CMD", "mysqladmin", "ping", "-h", "localhost"],
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 5,
                    },
                }
            }

        else:  # mariadb
            return {
                db.name: {
                    "image": f"mariadb:{db.version}",
                    "container_name": container,
                    "restart": "always",
                    "environment": {
                        "MARIADB_ROOT_PASSWORD": password,
                        "MARIADB_DATABASE": db.database,
                        "MARIADB_USER": db.user,
                        "MARIADB_PASSWORD": password,
                    },
                    "volumes": [f"{volume}:/var/lib/mysql"],
                    "ports": [f"{db.port}:3306"],
                    "healthcheck": {
                        "test": ["CMD", "healthcheck.sh", "--connect"],
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 5,
                    },
                }
            }

    def compose_volumes(self, config: OpalConfig) -> dict:
        return {f"{config.stack_name}-{self.db.name}-data": None}

    def opal_env_vars(self, config: OpalConfig, secrets: dict[str, str]) -> dict:
        db = self.db
        prefix = db.name.upper().replace("-", "_")
        pw_key = f"{prefix}_PASSWORD"
        password = secrets[pw_key]

        internal_port = {
            DatabaseType.POSTGRES: "5432",
            DatabaseType.MYSQL: "3306",
            DatabaseType.MARIADB: "3306",
        }

        return {
            f"{prefix}_HOST": db.name,
            f"{prefix}_PORT": internal_port[db.type],
            f"{prefix}_DATABASE": db.database,
            f"{prefix}_USER": db.user,
            f"{prefix}_PASSWORD": password,
        }
