"""Local preflight. Does not contact Telegram or print credentials."""
import os

from aiogram.utils.token import validate_token
from dotenv import load_dotenv

from bot.config import ROOT, Config, load_json
from bot.domain import validate_rules
from bot.events import validate_catalog


def check():
    load_dotenv(ROOT / ".env")
    config = Config.from_env()
    validate_token(config.token)
    validate_rules(load_json(os.environ.get("RULES_PATH", "config/rules.json")))
    events = load_json(os.environ.get("EVENTS_PATH", "config/events.json"))
    validate_catalog(events)
    print("Settings OK. Token format checked locally; no Telegram connection made.")
    print(f"Master IDs: {', '.join(map(str, sorted(config.masters)))}")
    print(f"Camp group: {config.chat_id}; topic: {config.thread_id}; events: {len(events)}")


if __name__ == "__main__":
    try:
        check()
    except Exception:
        # Some token validators embed input in error strings. Never print it.
        print("Configuration invalid. Check BOT_TOKEN, MASTER_IDS, CAMP_CHAT_ID, CAMP_THREAD_ID and JSON files.")
        raise SystemExit(1)
