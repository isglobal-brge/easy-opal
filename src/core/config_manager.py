import json
from pathlib import Path
from typing import Dict, Any, List

CONFIG_FILE = Path("config.json")
BACKUPS_DIR = Path("backups")
DATA_DIR = Path("data")
MONGO_DATA_DIR = DATA_DIR / "mongo"
NGINX_DIR = DATA_DIR / "nginx"
CERTS_DIR = NGINX_DIR / "certs"
NGINX_CONF_DIR = NGINX_DIR / "conf"


def get_default_config() -> Dict[str, Any]:
    """Returns the default configuration dictionary."""
    return {
        "stack_name": "easy-opal",
        "hosts": ["localhost", "127.0.0.1"],
        "opal_external_port": 443,
        "opal_admin_password": "password",
        "profiles": [
            {
                "name": "rock",
                "image": "datashield/rock-base",
                "tag": "latest"
            }
        ],
        "ssl": {
            "strategy": "self-signed",
            "cert_path": str(CERTS_DIR / "opal.crt"),
            "key_path": str(CERTS_DIR / "opal.key"),
            "le_email": ""
        }
    }

def init_config() -> Dict[str, Any]:
    """Initializes and saves the default configuration."""
    config = get_default_config()
    save_config(config)
    return config

def save_config(config: Dict[str, Any]) -> None:
    """Saves the configuration dictionary to the config file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def load_config() -> Dict[str, Any]:
    """Loads the configuration from the config file."""
    if not CONFIG_FILE.exists():
        return init_config()
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def ensure_directories_exist():
    """Ensures that all necessary data and backup directories exist."""
    BACKUPS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    MONGO_DATA_DIR.mkdir(exist_ok=True)
    NGINX_DIR.mkdir(exist_ok=True)
    CERTS_DIR.mkdir(exist_ok=True)
    NGINX_CONF_DIR.mkdir(exist_ok=True) 