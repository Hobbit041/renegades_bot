from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User

from bot.config import Config
from bot.db import Store
from bot.handlers import make_router
from bot.main import Application
from conftest import add_worker


class FakeTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        markup = getattr(method, "reply_markup", None)
        return Message(message_id=100, date=datetime.now(timezone.utc), chat=Chat(id=int(method.chat_id), type="private"),
                       text=getattr(method, "text", ""), reply_markup=markup if isinstance(markup, InlineKeyboardMarkup) else None)

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


@pytest.fixture
async def app(tmp_path, rules):
    config = Config("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789", frozenset({1}), -100100, 42,
                    str(tmp_path / "test.sqlite3"), ZoneInfo("Europe/Moscow"))
    store = Store(config.db_path)
    await store.init(rules)
    bot = Bot(config.token, session=FakeTelegram())
    app = Application(config, rules, store, bot)
    app.dp = Dispatcher()
    app.dp.include_router(make_router(app))
    yield app
    await bot.session.close()
    await store.close()


async def message(app, uid, text, group=False):
    msg = Message(message_id=10, date=datetime.now(timezone.utc), text=text,
                  from_user=User(id=uid, is_bot=False, first_name="Игрок"),
                  chat=Chat(id=-100100 if group else uid, type="supergroup" if group else "private"))
    await app.dp.feed_update(app.bot, Update(update_id=10, message=msg))


async def callback(app, uid, data, key="unique", group=False):
    query = CallbackQuery(id=key, chat_instance="instance", from_user=User(id=uid, is_bot=False, first_name="Игрок"),
        data=data, message=Message(message_id=11, date=datetime.now(timezone.utc), text="old",
        chat=Chat(id=-100100 if group else uid, type="supergroup" if group else "private")))
    await app.dp.feed_update(app.bot, Update(update_id=11, callback_query=query))


async def test_group_ignored_for_messages_and_callbacks(app):
    await message(app, 1, "/start", True)
    await callback(app, 1, "planning", group=True)
    assert not app.bot.session.calls


async def topic_command(app, chat_id, thread_id, command="/camp"):
    msg = Message(message_id=30, date=datetime.now(timezone.utc), text=command,
                  from_user=User(id=10, is_bot=False, first_name="Игрок"),
                  chat=Chat(id=chat_id, type="supergroup"), message_thread_id=thread_id, is_topic_message=True)
    await app.dp.feed_update(app.bot, Update(update_id=30, message=msg))


async def test_camp_command_in_configured_topic_is_read_only_and_current(app):
    from bot.service import active_check
    async with app.store.transaction() as state:
        active_check(state, "camp-test", "gold", 10, "Находка", app.rules)
    before = await app.store.snapshot()
    await topic_command(app, app.config.chat_id, app.config.thread_id)
    assert (await app.store.snapshot()) == before
    report = app.bot.session.calls[-1]
    assert isinstance(report, SendMessage)
    assert report.chat_id == app.config.chat_id and report.message_thread_id == app.config.thread_id
    assert "Золото: 1258 зм" in report.text and "🏕️ Лагерь" in report.text
    assert report.reply_markup is None
    assert report.entities


@pytest.mark.parametrize("chat_id,thread_id", [(-100100, 24), (-100999, 42), (-100100, None)])
async def test_camp_command_ignored_outside_configured_topic(app, chat_id, thread_id):
    await topic_command(app, chat_id, thread_id)
    assert not app.bot.session.calls


async def test_who_command_lists_current_jobs_without_changing_state(app):
    from bot.presentation import jobs_text
    from bot.domain import active_characters
    before = await app.store.snapshot()
    await topic_command(app, app.config.chat_id, app.config.thread_id, "/who")
    report = app.bot.session.calls[-1]
    assert report.message_thread_id == app.config.thread_id
    assert "Кто чем сейчас занят" in report.text
    for c in active_characters(before):
        assert f"{c['name']}: {jobs_text(c)}" in report.text
    assert await app.store.snapshot() == before
    assert report.reply_markup is None


@pytest.mark.parametrize("chat_id,thread_id", [(-100100, 24), (-100999, 42), (-100100, None)])
async def test_who_command_ignored_outside_configured_topic(app, chat_id, thread_id):
    await topic_command(app, chat_id, thread_id, "/who")
    assert not app.bot.session.calls


async def test_master_gets_different_home(app):
    await message(app, 1, "/start")
    master = app.bot.session.calls[-1].reply_markup
    assert master.keyboard[0][0].text == "Жители"
    await message(app, 10, "/start")
    player = app.bot.session.calls[-1].reply_markup
    assert player.keyboard[0][0].text == "Персонаж"


async def test_player_cannot_forge_master_buttons(app):
    before = await app.store.snapshot()
    await callback(app, 10, "life:luxury")
    assert (await app.store.snapshot())["camp"] == before["camp"]
    assert "мастеру" in app.bot.session.calls[-1].text


async def test_new_character_notifies_master_and_needs_approval(app):
    await callback(app, 10, "new")
    await message(app, 10, "Герра")
    state = await app.store.snapshot()
    char = next(c for c in state["characters"].values() if c["owner"] == 10)
    assert not char["approved"]
    assert any(o["target"] == 1 for o in state["outbox"])
    await callback(app, 1, "grant:" + char["id"] + ":hunt")
    await message(app, 1, "2 15")
    await callback(app, 1, "approve:" + char["id"])
    state = await app.store.snapshot()
    assert state["characters"][char["id"]]["approved"]
    assert state["users"]["10"]["character"] == char["id"]


async def test_repeated_telegram_callback_does_not_double_count(app):
    async with app.store.transaction() as state:
        cid = add_worker(state, "hunt", owner=10)
        state["characters"][cid]["actions"] = {}
    await callback(app, 10, "act:" + cid + ":hunt", "same")
    await callback(app, 10, "act:" + cid + ":hunt", "same")
    assert (await app.store.snapshot())["characters"][cid]["actions"] == {"hunt": 1}


async def test_session_confirmation_and_duplicate(app):
    await message(app, 1, "Начать сессию")
    await message(app, 1, "7")
    d = (await app.store.snapshot())["users"]["1"]["dialog"]
    await callback(app, 1, "run:" + d["operation"])
    await callback(app, 1, "run:" + d["operation"])
    state = await app.store.snapshot()
    assert state["camp"]["day"] == 7
    assert len(state["sessions"]) == 1
    assert sum(o["target"] == "camp" for o in state["outbox"]) == 1


async def test_revision_change_invalidates_session_preview(app):
    await message(app, 1, "Начать сессию")
    await message(app, 1, "7")
    operation = (await app.store.snapshot())["users"]["1"]["dialog"]["operation"]
    await callback(app, 1, "life:comfortable")
    await callback(app, 1, "run:" + operation)
    assert (await app.store.snapshot())["camp"]["day"] == 0


async def test_reports_only_target_configured_topic(app):
    await message(app, 1, "Начать сессию")
    await message(app, 1, "1")
    operation = (await app.store.snapshot())["users"]["1"]["dialog"]["operation"]
    await callback(app, 1, "run:" + operation)
    await app.deliver()
    posts = [m for m in app.bot.session.calls if isinstance(m, SendMessage) and m.chat_id == -100100]
    assert posts and all(m.message_thread_id == 42 for m in posts)
    count = len(posts)
    await app.deliver()
    assert len([m for m in app.bot.session.calls if isinstance(m, SendMessage) and m.chat_id == -100100]) == count


async def test_spend_and_meal_are_confirmed_once(app):
    await callback(app, 1, "spend:components")
    await message(app, 1, "5 | Успех крафта")
    state = await app.store.snapshot()
    op = state["users"]["1"]["dialog"]["operation"]
    assert state["camp"]["components"] == 42
    await callback(app, 1, "checkrun:" + op)
    await callback(app, 1, "checkrun:" + op)
    assert (await app.store.snapshot())["camp"]["components"] == 37
    state = await app.store.snapshot()
    reports = [o for o in state["outbox"] if o["target"] == "camp"]
    assert len(reports) == 1
    assert "🏕️ Состояние лагеря сейчас" in reports[0]["text"]
    assert "🧪 Компоненты: 37" in reports[0]["text"]
    assert state["camp"]["day"] == 0
    await callback(app, 1, "meal")
    await message(app, 1, "2 1 3")
    state = await app.store.snapshot()
    op = state["users"]["1"]["dialog"]["operation"]
    await callback(app, 1, "checkrun:" + op)
    state = await app.store.snapshot()
    assert state["camp"]["components"] == pytest.approx(33.4)
    assert state["camp"]["morale"] == 57
    reports = [o for o in state["outbox"] if o["target"] == "camp"]
    assert len(reports) == 2
    assert "🧪 Компоненты: 33.4" in reports[-1]["text"]
    assert "🔥 Мораль: 57%" in reports[-1]["text"]


async def test_concurrent_delivery_does_not_send_twice(app):
    import asyncio
    from bot.service import enqueue
    async with app.store.transaction() as state:
        enqueue(state, "camp", "Report")
    await asyncio.gather(app.deliver(), app.deliver())
    assert sum(isinstance(m, SendMessage) and m.chat_id == -100100 for m in app.bot.session.calls) == 1


async def test_personal_event_delivered_with_buttons_and_resolved_once(app):
    import time
    from bot.config import load_json
    from bot.service import draw_events, user
    class Always:
        def random(self): return 0
        def choice(self, values): return values[0]
    async with app.store.transaction() as state:
        cid = add_worker(state, "hunt", owner=10)
        state["characters"][cid]["event_skills"] = {"diplomacy": {"rank": 2, "modifier": 15}}
        user(state, 10)["events"] = True
        draw_events(state, load_json("config/events.json"), "2026-11-05", Always(), app.rules)
        eid = next(iter(state["event_instances"]))
    await app.deliver()
    post = next(m for m in app.bot.session.calls if isinstance(m, SendMessage) and m.chat_id == 10)
    assert post.reply_markup.inline_keyboard[0][0].callback_data == f"evt:{eid}:0"
    await callback(app, 10, f"evt:{eid}:0", key="one")
    first = (await app.store.snapshot())["camp"]
    await callback(app, 10, f"evt:{eid}:1", key="two")
    assert (await app.store.snapshot())["camp"] == first
    assert len((await app.store.snapshot())["events"]) == 1
    state = await app.store.snapshot()
    assert sum(o["target"] == "camp" for o in state["outbox"]) == 1
    await app.deliver()
    reports = [m for m in app.bot.session.calls if isinstance(m, SendMessage) and m.chat_id == -100100]
    assert len(reports) == 1 and reports[0].message_thread_id == 42
    assert reports[0].reply_markup is None
    assert "Событие лагеря: Песня у костра" in reports[0].text
    assert "Персонаж:" in reports[0].text and "Результат:" in reports[0].text
    assert "Проверка:" not in reports[0].text
    assert "🏕️ Состояние лагеря сейчас" in reports[0].text


async def test_expired_event_callback_and_unsent_event_queue(app):
    import time
    from bot.config import load_json
    from bot.events import issue_event
    from bot.service import enqueue
    async with app.store.transaction() as state:
        cid = add_worker(state, "hunt", owner=10)
        entry, markup = issue_event(state, 10, state["characters"][cid], load_json("config/events.json")[0],
                                    time.time()-86401, app.rules)
        enqueue(state, 10, "Expired", markup=markup, event_id=entry["id"])
        eid = entry["id"]
    await app.deliver()
    assert not any(isinstance(m, SendMessage) for m in app.bot.session.calls)
    await callback(app, 10, f"evt:{eid}:0")
    assert app.bot.session.calls[-1].text == "Событие уже устарело"
    state = await app.store.snapshot()
    assert not state["events"]
