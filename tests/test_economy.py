import copy
import random

import pytest

from bot.domain import advance, efficiency, item_drop, production_tier, validate_rules
from conftest import add_worker


def test_modifier_scale_and_validation(rules):
    validate_rules(rules)
    assert efficiency(5, rules) == .4
    assert efficiency(10, rules) == pytest.approx(2/3)
    assert efficiency(15, rules) == efficiency(50, rules) == 1
    assert .4 < efficiency(7, rules) < 2/3


@pytest.mark.parametrize("resource,action,expected", [("food", "hunt", 40), ("components", "components", 32), ("materials", "materials", 200)])
@pytest.mark.parametrize("npc", [True, False])
def test_four_high_workers_produce_expected_stock(state, rules, resource, action, expected, npc):
    state["camp"][resource] = 0
    state["camp"]["policies"] = dict.fromkeys(("food", "components", "materials"), "none")
    for i in range(4):
        add_worker(state, action, owner=None if npc else i+1)
    advance(state, 7, rules, random.Random(1))
    assert state["camp"][resource] == pytest.approx(expected)


@pytest.mark.parametrize("lifestyle,days", [("comfortable", 28), ("good", 28), ("luxury", 7)])
def test_morale_targets(state, rules, lifestyle, days):
    rules["weekly"]["food_person"] = 0
    state["camp"].update(morale=0, gold=100000, lifestyle=lifestyle)
    for _ in range(4):
        add_worker(state, "morale")
    advance(state, days - 1, rules, random.Random(2))
    assert state["camp"]["morale"] < 100
    advance(state, 1, rules, random.Random(3))
    assert state["camp"]["morale"] == pytest.approx(100, abs=.0001)


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


@pytest.mark.parametrize("policy,loss", [("none",10/7),("moderate",1),("increased",1/7)])
def test_four_average_collectors_lose_components_daily(state, rules, policy, loss):
    state["camp"].update(components=100, gold=100000)
    state["camp"]["policies"]["components"] = policy
    for _ in range(4):
        add_worker(state, "components", modifier=10)
    for day in range(1, 8):
        advance(state, 1, rules)
        assert state["camp"]["components"] == pytest.approx(100-day*loss)


@pytest.mark.parametrize("food,hungry", [(0,True),(29.99,True),(30,False),(60,False)])
def test_low_food_hunger_threshold(state, rules, food, hungry):
    rules["weekly"]["food_person"] = 0
    state["camp"].update(food=food, morale=60, lifestyle="luxury", gold=100000)
    state["camp"]["policies"]["food"] = "none"
    for _ in range(4):
        add_worker(state, "morale")
    advance(state, 1, rules)
    assert (state["camp"]["morale"] < 60) == hungry


@pytest.mark.parametrize("morale,multiplier", [(0,1),(29.99,1),(30,1),(69.99,1),(70,1),(100,1)])
def test_morale_never_changes_passive_job_efficiencies(state, rules, morale, multiplier):
    from bot.domain import ACTIONS, workforce
    state["camp"]["morale"] = morale
    for action in ACTIONS:
        add_worker(state, action)
    rates, _, _ = workforce(state, rules)
    for action in ACTIONS:
        assert rates[action] == pytest.approx(rules["yield_high"][action]*multiplier)


def test_morale_changes_within_a_session_do_not_change_yields(state, rules):
    rules["weekly"]["food_person"] = 0
    state["camp"].update(morale=69, lifestyle="comfortable", gold=100000)
    for _ in range(4):
        add_worker(state, "morale")
    add_worker(state, "gold")
    result = advance(state, 2, rules)
    assert result["collected"]["gold"] == pytest.approx(6.3*2/7)


def test_chunk_invariance_and_hidden_cycles(state, rules):
    a, b = copy.deepcopy(state), copy.deepcopy(state)
    ra, rb = random.Random(20), random.Random(20)
    result = advance(a, 21, rules, ra)
    last_items = []
    for _ in range(21):
        last = advance(b, 1, rules, rb)
        if last["items"]:
            last_items = last["items"]
    assert a["camp"] == b["camp"]
    assert result["items"] == last_items
    assert a["contributions"] == b["contributions"]


def test_visibility_floor_and_material_building_cost(state, rules):
    state["camp"].update(residents=50, visibility=0)
    state["camp"]["buildings"] = [{"upkeep": 7}]
    state["camp"]["policies"]["materials"] = "none"
    before = state["camp"]["materials"]
    advance(state, 1, rules)
    assert state["camp"]["visibility"] == 50
    assert before - state["camp"]["materials"] == pytest.approx((23*2.5+7)/7)


@pytest.mark.parametrize("components,days,count", [
    (0,0,0), (10,0,0), (10.01,0,0), (29.99,0,0), (30,0,0), (30.01,14,1), (49.99,14,1),
    (50,10.5,1), (69.99,10.5,1), (70,7,1), (89.99,7,1), (90,7,2), (100,7,2),
])
def test_production_tiers_and_level_one_items(rules, components, days, count):
    assert production_tier(components, rules)["days"] == days
    drops, _ = item_drop(components, rules, random.Random(3))
    assert len(drops) == count
    assert all(any(x["name"] == name and x["level"] == 1 for x in rules["items"]) for name in drops)


@pytest.mark.parametrize("components,ready_day,count", [(100,7,2),(80,7,1),(60,11,1),(40,14,1)])
def test_production_waits_and_keeps_progress(state, rules, components, ready_day, count):
    rules["weekly"]["components"] = 0
    state["camp"]["policies"]["components"] = "none"
    state["camp"]["components"] = components
    assert not advance(state, ready_day-1, rules)["items"]
    result = advance(state, 1, rules)
    assert len(result["items"]) == count
    assert state["camp"]["components"] == components


def test_speed_changes_keep_work_and_report_latest_batch(state, rules):
    rules["weekly"]["components"] = 0
    state["camp"]["policies"]["components"] = "none"
    state["camp"]["components"] = 40
    advance(state, 7, rules)
    assert state["camp"]["production_progress"] == pytest.approx(.5)
    state["camp"]["components"] = 100
    result = advance(state, 5, rules)
    assert len(result["items"]) == 2  # Finished on day 11, still reported on day 12.
    assert state["camp"]["production_progress"] == pytest.approx(3/14)
    assert not advance(state, 1, rules)["items"]  # The previous batch is not issued twice.


@pytest.mark.parametrize("components,note", [(0,"Всё производство остановлено"),(10,"Всё производство остановлено"),(10.01,"Тельга не производит предметы"),(30,"Тельга не производит предметы")])
def test_low_components_pause_and_resume_the_same_batch(state, rules, components, note):
    rules["weekly"]["components"] = 0
    state["camp"]["policies"]["components"] = "none"
    state["camp"]["components"] = 40
    advance(state, 7, rules)
    progress = state["camp"]["production_progress"]
    state["camp"]["components"] = components
    result = advance(state, 28, rules)
    assert not result["items"]
    assert result["item_note"] == note
    assert state["camp"]["production_progress"] == progress
    state["camp"]["components"] = 40
    assert len(advance(state, 7, rules)["items"]) == 1


def test_legacy_cycle_starts_new_progress_without_changing_resources(state, rules):
    state["camp"].pop("production_progress", None)
    state["camp"].update(cycle_due=999, components=100)
    rules["weekly"]["components"] = 0
    state["camp"]["policies"]["components"] = "none"
    assert len(advance(state, 7, rules)["items"]) == 2
    assert "cycle_due" not in state["camp"]
    assert state["camp"]["components"] == 100


def test_old_balance_json_uses_new_production_defaults(state, rules):
    rules["production"] = {"ready_probabilities": [.1]+[.15]*6, "level2_chance": .5}
    validate_rules(rules)
    assert production_tier(100, rules)["quantity"] == 2
    assert len(item_drop(100, rules, random.Random(1))[0]) == 2
