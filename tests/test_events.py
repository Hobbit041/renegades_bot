import copy
import json
import time
from pathlib import Path

import pytest

from bot.events import character_skills, issue_event, resolve_event, validate_catalog
from bot.service import draw_events, user
from conftest import add_worker


class Fixed:
    def __init__(self, value):
        self.value, self.calls = value, 0
    def random(self):
        self.calls += 1
        return self.value
    def choice(self, values):
        return values[0]


@pytest.fixture
def catalog():
    return json.loads((Path(__file__).parent.parent / "config/events.json").read_text(encoding="utf-8"))


def offer(state, rules, event, modifier=10):
    cid = add_worker(state, "gold", owner=10)
    c = state["characters"][cid]
    c["imported_skills"] = {key: {"rank": 2, "modifier": modifier}
                            for key in ("survival", "hunting-lore", "perception", "diplomacy", "performance")}
    entry, buttons = issue_event(state, 10, c, event, 1000, rules)
    return entry, buttons


def test_catalog_and_all_actions_have_an_outcome(catalog):
    validate_catalog(catalog)
    assert len(catalog) >= 15
    assert sum(e["origin"] == "user" for e in catalog) == 3
    for e in catalog:
        for choice in e["choices"]:
            assert "success" in choice and "failure" in choice


def test_offer_not_filtered_by_strength(state, rules, catalog):
    # A gold worker with no relevant skill is still offered the campfire.
    add_worker(state, "gold", owner=10)
    user(state, 10)["events"] = True
    draw_events(state, catalog, "2026-11-05", Fixed(0), rules, now=1000)
    entry = next(iter(state["event_instances"].values()))
    assert entry["definition"]["id"] == "campfire"
    assert state["outbox"][0]["markup"]["inline_keyboard"][-1][0]["callback_data"].endswith(":skip")


def test_skip_has_no_camp_effects_or_public_journal(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[0])
    before = copy.deepcopy(state["camp"])
    rng = Fixed(0)
    result = resolve_event(state, 10, entry["id"], "skip", rules, now=1001, rng=rng)
    assert result["status"] == "skipped" and rng.calls == 0
    assert state["camp"] == before and not state["events"] and not state["contributions"]
    assert not state["outbox"]
    # Skipping consumes the encounter; an old success button cannot be used.
    assert resolve_event(state, 10, entry["id"], "0", rules, now=1002, rng=rng) == result


@pytest.mark.parametrize("now", [1000+86400, 1000+86401])
def test_expiry_exactly_24h(state, rules, catalog, now):
    entry, _ = offer(state, rules, catalog[0])
    before = copy.deepcopy(state["camp"])
    result = resolve_event(state, 10, entry["id"], "0", rules, now=now, rng=Fixed(0))
    assert result["text"] == "Событие уже устарело"
    assert state["camp"] == before and not state["events"]
    assert not state["outbox"]


@pytest.mark.parametrize("roll,outcome,delta", [(0,"success",3),(.68,"critical_failure",-3),(.99,"failure",-1)])
def test_campfire_outcomes_and_absolute_seven_percent(state, rules, catalog, roll, outcome, delta):
    entry, _ = offer(state, rules, catalog[0])
    result = resolve_event(state, 10, entry["id"], "0", rules, now=1001, rng=Fixed(roll))
    assert result["probability"] == pytest.approx(2/3)
    assert result["critical_probability"] == .07
    assert result["outcome"] == outcome
    assert state["camp"]["morale"] == 55+delta
    assert len(state["events"]) == 1


def test_critical_reserves_seven_percent_even_at_high_modifier(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[0], modifier=30)
    result = resolve_event(state, 10, entry["id"], "1", rules, now=1001, rng=Fixed(.95))
    assert result["probability"] == pytest.approx(.93) and result["outcome"] == "critical_failure"


def test_normal_maximum_is_100_and_hard_penalty_is_15_points(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[2], modifier=15)
    result = resolve_event(state, 10, entry["id"], "1", rules, now=1001, rng=Fixed(.8))
    assert result["probability"] == .85 and result["effects"]["gold"] == 10
    other, _ = offer(state, rules, catalog[2], modifier=15)
    result = resolve_event(state, 10, other["id"], "0", rules, now=1001, rng=Fixed(.99))
    assert result["probability"] == 1 and result["effects"]["gold"] == 5


def test_stranger_failure_clamps_to_remaining_treasury(state, rules, catalog):
    state["camp"]["gold"] = 3
    entry, _ = offer(state, rules, catalog[2])
    result = resolve_event(state, 10, entry["id"], "0", rules, now=1001, rng=Fixed(.99))
    assert result["effects"]["gold"] == -3 and state["camp"]["gold"] == 0


def test_best_alternative_and_no_penalty_for_syfer_failure(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[1])
    entry["skills"]["survival"]["modifier"] = 5
    entry["skills"]["hunting-lore"]["modifier"] = 15
    result = resolve_event(state, 10, entry["id"], "0", rules, now=1001, rng=Fixed(.9))
    assert result["skill"] == "hunting-lore" and result["outcome"] == "success"
    other, _ = offer(state, rules, catalog[1])
    before = copy.deepcopy(state["camp"])
    result = resolve_event(state, 10, other["id"], "1", rules, now=1001, rng=Fixed(.99))
    assert result["outcome"] == "failure" and state["camp"] == before


def test_second_click_never_rerolls_or_changes_choice(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[2])
    a, b = Fixed(0), Fixed(.99)
    first = resolve_event(state, 10, entry["id"], "0", rules, now=1001, rng=a)
    before = copy.deepcopy(state["camp"])
    assert resolve_event(state, 10, entry["id"], "1", rules, now=1002, rng=b) == first
    assert state["camp"] == before and b.calls == 0 and len(state["events"]) == 1
    assert sum(o["key"] == "event-result:" + entry["id"] for o in state["outbox"]) == 1


def test_owner_character_binding_and_config_snapshot(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[0])
    with pytest.raises(ValueError, match="другого"):
        resolve_event(state, 11, entry["id"], "0", rules, now=1001)
    original = entry["character"]
    add_worker(state, "hunt", owner=10)
    rules["modifier_scale"] = [[0,0],[1,1]]
    result = resolve_event(state, 10, entry["id"], "0", rules, now=1001, rng=Fixed(0))
    assert result["probability"] == pytest.approx(2/3)
    assert state["events"][-1]["character"] == original


def test_missing_modifier_does_not_consume_offer_and_can_be_filled(state, rules, catalog):
    entry, _ = offer(state, rules, catalog[0])
    del entry["skills"]["diplomacy"]
    c = state["characters"][entry["character"]]
    del c["imported_skills"]["diplomacy"]
    with pytest.raises(ValueError, match="модификатора"):
        resolve_event(state, 10, entry["id"], "0", rules, now=1001)
    assert entry["status"] == "active"
    c["event_skills"] = {"diplomacy": {"rank": 2, "modifier": 10}}
    assert resolve_event(state, 10, entry["id"], "0", rules, now=1001, rng=Fixed(0))["outcome"] == "success"


def test_master_event_override_has_priority_over_old_passive_grant():
    character = {"skills": {"morale": {"rank": 2, "modifier": 10, "source_skill": "diplomacy"}},
                 "imported_skills": {"diplomacy": {"rank": 2, "modifier": 12}},
                 "event_skills": {"diplomacy": {"rank": 2, "modifier": 15}}}
    assert character_skills(character)["diplomacy"]["modifier"] == 15


async def test_pending_events_and_one_resolution_survive_restart(tmp_path, rules, catalog):
    from bot.db import Store
    path = tmp_path / "camp.sqlite3"
    store = Store(path)
    await store.init(rules)
    async with store.transaction() as s:
        entry, _ = offer(s, rules, catalog[0])
        eid = entry["id"]
    await store.close()
    store = Store(path)
    async with store.transaction() as s:
        result = resolve_event(s, 10, eid, "0", rules, now=1001, rng=Fixed(0))
        assert result["effects"]["morale"] == 3
    await store.close()
    store = Store(path)
    async with store.transaction() as s:
        resolve_event(s, 10, eid, "1", rules, now=1002, rng=Fixed(.99))
        assert s["camp"]["morale"] == 58 and len(s["events"]) == 1
    await store.close()
