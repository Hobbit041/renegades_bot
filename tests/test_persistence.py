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
        state["camp"].update(day=4, gold=1530, food=72)
    await store.close()
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as state:
        assert state["characters"][cid]["actions"] == {"hunt": 1}
        assert (state["camp"]["day"], state["camp"]["gold"], state["camp"]["food"]) == (4, 1530, 72)
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


async def test_population_tracks_approved_characters_and_extra_residents(tmp_path, rules):
    store = Store(tmp_path / "population.sqlite3")
    await store.init(rules)
    original = await store.snapshot()
    async with store.transaction() as state:
        first = add_worker(state, "hunt", owner=10)
        second = add_worker(state, "gold", owner=10)
        pending = create_character(state, 11, "Ожидает", [])
    current = await store.snapshot()
    assert current["camp"]["residents"] == original["camp"]["residents"] + 2
    async with store.transaction() as state:
        state["characters"][pending["id"]]["approved"] = True
        state["camp"]["extra_residents"] = 7
    current = await store.snapshot()
    assert current["camp"]["residents"] == len([c for c in current["characters"].values() if c["approved"]]) + 7
    await store.close()
    store = Store(tmp_path / "population.sqlite3")
    await store.init(rules)
    assert (await store.snapshot())["camp"] == current["camp"]
    await store.close()


@pytest.mark.parametrize("old_total", [7, 20])
def test_population_migrates_legacy_total_once(state, old_total):
    from bot.domain import active_characters, sync_population
    state["camp"].pop("extra_residents", None)
    state["camp"]["residents"] = old_total
    for _ in range(10):
        add_worker(state, "hunt")
    named = len(active_characters(state))
    sync_population(state)
    assert state["camp"]["extra_residents"] == max(0, old_total-named)
    assert state["camp"]["residents"] == max(old_total, named)
    add_worker(state, "hunt")
    sync_population(state)
    assert state["camp"]["residents"] == max(old_total, named)+1


async def test_production_progress_survives_sessions_and_restart(tmp_path, rules):
    from bot.service import start_session
    rules["weekly"]["components"] = 0
    path = tmp_path / "production.sqlite3"
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as state:
        state["camp"]["components"] = 100
        state["camp"]["policies"]["components"] = "none"
        result = start_session(state, 1, "three-days", 3, rules)
        assert not result["items"]
    await store.close()
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as state:
        assert state["camp"]["production_progress"] == pytest.approx(3/7)
        state["camp"]["components"] = 20
        result = start_session(state, 1, "paused", 28, rules)
        assert not result["items"]
        assert state["camp"]["production_progress"] == pytest.approx(3/7)
    await store.close()
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as state:
        assert state["camp"]["production_progress"] == pytest.approx(3/7)
        state["camp"]["components"] = 100
        result = start_session(state, 1, "four-days", 4, rules)
        assert len(result["items"]) == 2
    await store.close()
