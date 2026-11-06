import asyncio
import time

import pytest

from bot.db import Store
from bot.service import create_character, due_announcements, user
from conftest import add_worker


async def test_restart_preserves_deadlines_and_subscription(tmp_path, rules):
    path = tmp_path / "camp.sqlite3"
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as state:
        cid = add_worker(state, "hunt", owner=10)
        user(state, 10).update(events=True, announce_due=time.time()-1)
    await store.close()
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as state:
        due_announcements(state, time.time())
        assert user(state, 10)["events"]
        assert len(state["outbox"]) == 1
    await store.close()


async def test_atomic_rollback_and_concurrent_updates(tmp_path, rules):
    store = Store(tmp_path / "camp.sqlite3")
    await store.init(rules)
    with pytest.raises(ValueError):
        async with store.transaction() as state:
            state["camp"]["gold"] = -100
            raise ValueError("abort")
    assert (await store.snapshot())["camp"]["gold"] == 1248
    async def increase():
        async with store.transaction() as state:
            state["camp"]["gold"] += 1
    await asyncio.gather(*(increase() for _ in range(20)))
    assert (await store.snapshot())["camp"]["gold"] == 1268
    await store.close()
