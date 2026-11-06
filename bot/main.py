from __future__ import annotations

import asyncio
import glob
import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# bothost may execute `python bot/main.py`, exactly as with Botopros.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for site in glob.glob(str(ROOT / ".venv/lib/python*/site-packages")):
    if site not in sys.path:
        sys.path.append(site)

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from bot.config import Config, load_json
from bot.db import Store
from bot.domain import validate_rules
from bot.events import validate_catalog
from bot.handlers import make_router
from bot.logging_setup import configure_logging
from bot.presentation import rich_chunks
from bot.service import draw_events, due_announcements, enqueue

log = logging.getLogger(__name__)


class Application:
    def __init__(self, config, rules, store, bot):
        self.config, self.rules, self.store, self.bot = config, rules, store, bot
        self.rules_path = os.environ.get("RULES_PATH", "config/rules.json")
        self.events_path = os.environ.get("EVENTS_PATH", "config/events.json")
        self.rng = random.Random()
        self.in_flight = set()
        self.delivery_lock = asyncio.Lock()

    async def tick(self):
        task = asyncio.current_task()
        self.in_flight.add(task)
        try:
            await self._tick()
        finally:
            self.in_flight.discard(task)

    async def _tick(self):
        try:
            now = datetime.now(self.config.timezone)
            try:
                catalog = load_json(self.events_path)
                validate_catalog(catalog)
            except (ValueError, OSError):
                log.exception("Каталог событий некорректен; остальные уведомления работают")
                catalog = []
            async with self.store.transaction() as state:
                due_announcements(state, time.time())
                if (now.hour, now.minute) >= (self.config.event_hour, self.config.event_minute):
                    draw_events(state, catalog, now.date().isoformat(), self.rng, self.rules)
        except Exception:
            # A malformed future event catalog must not block session reports.
            log.exception("Не удалось подготовить уведомления")
        await self.deliver()

    async def deliver(self):
        async with self.delivery_lock:
            await self._deliver()

    async def _deliver(self):
        snapshot = await self.store.snapshot()
        now = time.time()
        for row in snapshot["outbox"]:
            if row["sent"] or row.get("retry_at", 0) > now:
                continue
            if row.get("event_id"):
                entry = snapshot.get("event_instances", {}).get(row["event_id"])
                if not entry or entry["status"] != "active" or now >= entry["expires_at"]:
                    async with self.store.transaction() as state:
                        stored = next(x for x in state["outbox"] if x["id"] == row["id"])
                        stored.update(sent=True, error="event_inactive")
                    continue
            target = row["target"]
            chat = self.config.chat_id if target == "camp" else int(target)
            chunks = list(rich_chunks(row["text"], row.get("entities")))
            for index in range(row.get("part", 0), len(chunks)):
                try:
                    await self.bot.send_message(chat, **chunks[index], message_thread_id=self.config.thread_id if target == "camp" else None,
                                                reply_markup=row.get("markup") if index == len(chunks)-1 else None)
                except TelegramForbiddenError:
                    async with self.store.transaction() as state:
                        stored = next(x for x in state["outbox"] if x["id"] == row["id"])
                        stored.update(sent=target != "camp", error="TelegramForbiddenError", retry_at=time.time()+300)
                        if target == "camp":
                            for master in self.config.masters:
                                enqueue(state, master, "Не удалось опубликовать отчёт в теме лагеря. Проверьте права бота, CAMP_CHAT_ID и CAMP_THREAD_ID. Отчёт остаётся в очереди.", "camp-forbidden")
                        if target != "camp" and str(target) in state["users"]:
                            state["users"][str(target)]["events"] = False
                    if target == "camp":
                        log.error("Бот не может публиковать в теме лагеря. Проверьте права и ID темы.")
                    break
                except TelegramAPIError as exc:
                    delay = exc.retry_after if isinstance(exc, TelegramRetryAfter) else min(300, 10 * 2**min(row["attempts"], 5))
                    async with self.store.transaction() as state:
                        stored = next(x for x in state["outbox"] if x["id"] == row["id"])
                        stored["attempts"] += 1
                        stored["retry_at"] = time.time() + delay
                    log.warning("Сообщение %s остаётся в очереди: %s", row["id"], type(exc).__name__)
                    break
                else:
                    async with self.store.transaction() as state:
                        stored = next(x for x in state["outbox"] if x["id"] == row["id"])
                        stored["part"] = index + 1
                        stored["sent"] = index == len(chunks) - 1


async def main():
    load_dotenv(ROOT / ".env")
    log_dir = os.environ.get("LOG_DIR") or str(Path(os.environ.get("DATA_DIR") or ROOT) / "logs")
    configure_logging(log_dir)
    config = Config.from_env()
    rules = load_json(os.environ.get("RULES_PATH", "config/rules.json"))
    validate_rules(rules)
    store, bot = Store(config.db_path), Bot(config.token)
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    try:
        await store.init(rules)
        app = Application(config, rules, store, bot)
        dp = Dispatcher()
        dp.include_router(make_router(app))
        # All deadlines and daily draw markers are in the campaign DB; jobs need no job DB.
        scheduler.add_job(app.tick, "interval", seconds=10, max_instances=1, coalesce=True)
        scheduler.start()
        await bot.delete_webhook(drop_pending_updates=False)
        me = await bot.get_me()
        log.info("Бот @%s запущен; управление только в ЛС", me.username)
        await app.tick()
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            await asyncio.sleep(0)
            if 'app' in locals() and app.in_flight:
                await asyncio.gather(*app.in_flight, return_exceptions=True)
        await bot.session.close()
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
