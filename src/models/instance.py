from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstanceContext:
    """All paths for a single easy-opal deployment."""

    name: str
    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def secrets_path(self) -> Path:
        return self.root / "secrets.env"

    @property
    def compose_path(self) -> Path:
        return self.root / "docker-compose.yml"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def certs_dir(self) -> Path:
        return self.data_dir / "certs"

    @property
    def nginx_conf_dir(self) -> Path:
        return self.data_dir / "nginx"

    @property
    def nginx_html_dir(self) -> Path:
        return self.data_dir / "html"

    @property
    def letsencrypt_dir(self) -> Path:
        return self.data_dir / "letsencrypt"

    def ensure_dirs(self) -> None:
        """Create all required directories for this instance."""
        for d in [
            self.root,
            self.data_dir,
            self.certs_dir,
            self.nginx_conf_dir,
            self.nginx_html_dir,
            self.letsencrypt_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
