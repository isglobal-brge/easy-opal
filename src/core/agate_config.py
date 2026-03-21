"""Generate Agate's application-prod.yml for email configuration."""

import yaml
from pathlib import Path

from src.models.config import OpalConfig
from src.models.instance import InstanceContext


def generate_agate_config(config: OpalConfig, ctx: InstanceContext, secrets: dict[str, str]) -> None:
    """Generate /srv/conf/application-prod.yml for the Agate container."""
    if not config.agate.enabled:
        return

    agate_conf_dir = ctx.data_dir / "agate" / "conf"
    agate_conf_dir.mkdir(parents=True, exist_ok=True)

    mail: dict = {}

    if config.agate.mail_mode == "mailpit":
        mail = {
            "host": "mailpit",
            "port": 1025,
            "protocol": "smtp",
            "auth": False,
            "tls": False,
            "from": "opal-dev@localhost",
        }
    elif config.agate.mail_mode == "smtp":
        smtp = config.agate.smtp
        mail = {
            "host": smtp.host,
            "port": smtp.port,
            "user": smtp.user,
            "password": secrets.get("SMTP_PASSWORD", ""),
            "protocol": "smtp",
            "auth": smtp.auth,
            "tls": smtp.tls,
            "from": smtp.from_address,
        }
    else:
        mail = {
            "host": "localhost",
            "port": 25,
            "protocol": "smtp",
            "auth": False,
            "tls": False,
            "from": "noreply@localhost",
        }

    app_config = {"spring": {"mail": mail}}

    output = agate_conf_dir / "application-prod.yml"
    output.write_text(yaml.dump(app_config, default_flow_style=False, sort_keys=False))
