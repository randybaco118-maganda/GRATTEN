"""Tiny .env loader so FLASK_DEBUG, GRATTEN_SECRET_KEY, etc. work without extra packages."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path=None):
    """Load KEY=VALUE lines from .env into os.environ.

    Real environment variables always win over the file.
    """
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_flag(name, default="0"):
    return os.environ.get(name, default) == "1"
