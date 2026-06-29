from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
DATA_DIR = ROOT_DIR / "data"
PUBLIC_DIR = ROOT_DIR / "public"
DB_PATH = DATA_DIR / "achievement_tracker.sqlite3"


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()

STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "change-me")
PLAYER_REFRESH_TTL_SECONDS = int(os.getenv("PLAYER_REFRESH_TTL_SECONDS", "600"))
