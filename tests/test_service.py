import copy
import time

import pytest

from bot.domain import allocations
from bot.service import active_check, create_character, due_announcements, draw_events, owned, set_action, start_session, user
from conftest import add_worker


def test_weights_and_share_only_debounce(state):
    cid = add_worker(state, "hunt", owner=10)
    c = state["characters"][cid]
    c["skills"]["gold"] = {"rank": 1, "modifier": 10}
    c["actions"] = {}
    set_action(state, 10, "hunt", 300)
    set_action(state, 10, "hunt", 300)
    set_action(state, 10, "gold", 300)
    assert allocations(c) == {"hunt": 2/3, "gold": 1/3}
    due_announcements(state, time.time())
    assert not state["outbox"]
    due_announcements(state, time.time()+301)
    assert len(state["outbox"]) == 1
    set_action(state, 10, "gold", 300)
    assert allocations(c) == {"hunt": .5, "gold": .5}
    due_announcements(state, time.time()+301)
    assert len(state["outbox"]) == 1


def test_session_idempotency_and_immediate_action_selection(state, rules):
    cid = add_worker(state, "hunt", owner=10)
    state["camp"]["planning"] = False  # Campaign saved by the previous version.
    start_session(state, 1, "same", 7, rules)
    after = copy.deepcopy(state)
    start_session(state, 1, "same", 7, rules)
    assert state == after
    set_action(state, 10, "reset", 300)
    assert not allocations(state["characters"][cid])
    assert state["sessions"] == after["sessions"]


def test_ownership_and_insufficient_resources(state):
    cid = add_worker(state, "hunt", owner=10)
    with pytest.raises(ValueError):
        owned(state, 11, cid)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError):
        active_check(state, "a", "components", -10000, "craft")
    assert state == before
    active_check(state, "a", "components", -5, "craft")
    after = state["camp"]["components"]
    active_check(state, "a", "components", -5, "craft")
    assert state["camp"]["components"] == after


def test_new_character_not_automatically_approved_and_dm_notice(state):
    c = create_character(state, 10, "Герра", [1])
    assert not c["approved"] and c["owner"] == 10
    assert state["outbox"][0]["target"] == 1
    assert user(state, 10)["events"] is False


def test_events_once_per_real_day(state, rules):
    class Always:
        def random(self): return 0
        def choice(self, values): return values[0]
    user(state, 10)["events"] = True
    add_worker(state, "hunt", owner=10)
    catalog = [{"id": "test", "title": "Событие", "text": "Описание",
                "choices": [{"label": "Попробовать", "skills": ["survival"]}]}]
    draw_events(state, catalog, "2026-11-04", Always(), rules)
    draw_events(state, catalog, "2026-11-04", Always(), rules)
    assert len(state["outbox"]) == 1
    draw_events(state, catalog, "2026-11-05", Always(), rules)
    assert len(state["outbox"]) == 2
