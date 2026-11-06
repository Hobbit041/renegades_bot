import copy
import random

import pytest

from bot.domain import advance, efficiency, item_drop, validate_rules
from conftest import add_worker


def test_modifier_scale_and_validation(rules):
    validate_rules(rules)
    assert efficiency(5, rules) == .4
    assert efficiency(10, rules) == pytest.approx(2/3)
    assert efficiency(15, rules) == efficiency(50, rules) == 1
    assert .4 < efficiency(7, rules) < 2/3


@pytest.mark.parametrize("resource,action", [("food", "hunt"), ("components", "components"), ("materials", "materials")])
@pytest.mark.parametrize("npc", [True, False])
def test_four_any_workers_restore_full_stock(state, rules, resource, action, npc):
    state["camp"][resource] = 0
    state["camp"]["policies"] = dict.fromkeys(("food", "components", "materials"), "none")
    for i in range(4):
        add_worker(state, action, owner=None if npc else i+1)
    advance(state, 7, rules, random.Random(1))
    assert state["camp"][resource] == pytest.approx(rules["capacity"][resource])


@pytest.mark.parametrize("lifestyle,days", [("comfortable", 28), ("good", 28), ("luxury", 7)])
def test_morale_targets(state, rules, lifestyle, days):
    rules["weekly"]["food_person"] = 0
    state["camp"].update(morale=0, gold=100000, lifestyle=lifestyle)
    for _ in range(4):
        add_worker(state, "morale")
    advance(state, days - 1, rules, random.Random(2))
    assert state["camp"]["morale"] < 100
    advance(state, 1, rules, random.Random(3))
    assert state["camp"]["morale"] == pytest.approx(100)


def test_lifestyle_does_not_change_morale_without_workers(state, rules):
    rules["weekly"]["food_person"] = 0
    a, b = copy.deepcopy(state), copy.deepcopy(state)
    a["camp"].update(lifestyle="poor", gold=100000)
    b["camp"].update(lifestyle="luxury", gold=100000)
    advance(a, 7, rules, random.Random(1))
    advance(b, 7, rules, random.Random(1))
    assert a["camp"]["morale"] == b["camp"]["morale"] < 55


def test_hunger_beats_nine_morale_workers(state, rules):
    state["camp"].update(food=0, morale=80, lifestyle="luxury", gold=100000)
    state["camp"]["policies"]["food"] = "none"
    for _ in range(9):
        add_worker(state, "morale")
    advance(state, 1, rules)
    assert state["camp"]["morale"] < 80


def test_chunk_invariance_and_hidden_cycles(state, rules):
    a, b = copy.deepcopy(state), copy.deepcopy(state)
    ra, rb = random.Random(20), random.Random(20)
    result = advance(a, 21, rules, ra)
    for _ in range(21):
        last = advance(b, 1, rules, rb)
    assert a["camp"] == b["camp"]
    assert result["items"] == last["items"]
    assert result["item_note"] == last["item_note"]
    assert a["contributions"] == b["contributions"]


def test_visibility_floor_and_material_building_cost(state, rules):
    state["camp"].update(residents=50, visibility=0)
    state["camp"]["buildings"] = [{"upkeep": 7}]
    state["camp"]["policies"]["materials"] = "none"
    before = state["camp"]["materials"]
    advance(state, 1, rules)
    assert state["camp"]["visibility"] == 50
    assert before - state["camp"]["materials"] == pytest.approx((23*2.5+7)/7)


@pytest.mark.parametrize("components,count,third", [(29.99,0,0),(30,1,0),(49.99,1,0),(50,2,0),(70,3,1),(90,4,2),(100,4,2)])
def test_drop_thresholds(rules, components, count, third):
    drops, _ = item_drop(components, rules, random.Random(3))
    levels = [next(x["level"] for x in rules["items"] if x["name"] == name) for name in drops]
    assert len(levels) == count
    assert levels.count(3) == third
    assert levels.count(2) <= 1


def test_level_two_substitution_is_not_price_weighted(rules):
    found = set()
    for seed in range(100):
        drops, _ = item_drop(100, rules, random.Random(seed))
        found.add(any(x["level"] == 2 and x["name"] in drops for x in rules["items"]))
    assert found == {True, False}
