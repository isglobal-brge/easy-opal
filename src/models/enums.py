from enum import StrEnum


class SSLStrategy(StrEnum):
    SELF_SIGNED = "self-signed"
    LETSENCRYPT = "letsencrypt"
    MANUAL = "manual"
    NONE = "none"


class DatabaseType(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MARIADB = "mariadb"
