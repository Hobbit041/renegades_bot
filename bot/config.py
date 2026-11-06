from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def load_json(path: str | Path):
    path = Path(path)
    return json.loads((path if path.is_absolute() else ROOT / path).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Config:
    token: str
    masters: frozenset[int]
    chat_id: int
    thread_id: int
    db_path: str
    timezone: ZoneInfo
    event_hour: int = 19
    event_minute: int = 0
    debounce: int = 300

    @classmethod
    def from_env(cls):
        def required(name):
            value = os.environ.get(name, "").strip()
            if not value:
                raise ValueError(f"Заполните {name} в .env или панели хостинга")
            return value

        token = required("BOT_TOKEN")
        path = Path(os.environ.get("DB_PATH", "camp.sqlite3"))
        if not path.is_absolute():
            path = Path(os.environ.get("DATA_DIR") or ROOT) / path
        hour, minute = map(int, os.environ.get("EVENT_TIME", "19:00").split(":"))
        masters = frozenset(int(v.strip()) for v in required("MASTER_IDS").split(","))
        chat, thread = int(required("CAMP_CHAT_ID")), int(required("CAMP_THREAD_ID"))
        delay = int(os.environ.get("ACTION_DEBOUNCE_SECONDS", "300"))
        if not masters or any(v <= 0 for v in masters) or chat >= 0 or thread <= 0:
            raise ValueError("Проверьте MASTER_IDS, ID группы (отрицательный) и ID темы")
        if not 0 <= hour < 24 or not 0 <= minute < 60 or delay < 1:
            raise ValueError("Некорректное время событий или задержка публикации")
        return cls(token, masters, chat, thread, str(path),
                   ZoneInfo(os.environ.get("BOT_TIMEZONE", "Europe/Moscow")), hour, minute, delay)
