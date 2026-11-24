"""Runtime configuration read from environment variables.

REEFBERRY_DATA_DIR  where the database and secret key live
                    (default: ~/.local/share/reefberry; the Pi service uses /var/lib/reefberry)
REEFBERRY_HOST      address to listen on (default 0.0.0.0)
REEFBERRY_PORT      port to listen on (default 8080; the Pi service uses 80)
REEFBERRY_SIMULATE  set to 1 to use pretend plugs (for trying the app; nothing real is switched)
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    host: str
    port: int
    simulate: bool = False  # pretend plugs; nothing real is switched

    @property
    def db_path(self) -> Path:
        return self.data_dir / "reefberry.db"

    @property
    def icons_dir(self) -> Path:
        """Drop <name>.svg files here to replace or add equipment icons."""
        return self.data_dir / "icons"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"


def load_config() -> Config:
    default_dir = Path.home() / ".local" / "share" / "reefberry"
    data_dir = Path(os.environ.get("REEFBERRY_DATA_DIR", default_dir)).expanduser()
    return Config(
        data_dir=data_dir,
        host=os.environ.get("REEFBERRY_HOST", "0.0.0.0"),
        port=int(os.environ.get("REEFBERRY_PORT", "8080")),
        simulate=os.environ.get("REEFBERRY_SIMULATE", "") not in ("", "0"),
    )


def ensure_data_dir(cfg: Config) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(cfg.data_dir, 0o700)
    except OSError:
        pass  # Windows dev machines ignore POSIX modes


def load_or_create_secret(cfg: Config) -> str:
    """Session-signing key, generated once per install and kept private."""
    path = cfg.secret_path
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key
