from __future__ import annotations

import copy
import math
import random
import uuid

ACTIONS = {
    "hunt": "Охота", "cook": "Готовка", "components": "Сбор компонентов",
    "materials": "Сбор материалов", "morale": "Повышение морали",
    "hide": "Снижение заметности лагеря", "gold": "Зарабатывание денег",
}
RANKS = {0: "Не обучен", 1: "Обучен", 2: "Эксперт", 3: "Мастер", 4: "Легенда"}
POLICIES = {"none": "Не тратить", "moderate": "Умеренные", "increased": "Увеличенные"}
RESOURCES = {"gold": "Золото", "food": "Провиант", "components": "Компоненты",
             "materials": "Материалы", "morale": "Мораль", "visibility": "Заметность"}


def ident():
    return uuid.uuid4().hex[:12]


def finite(value, low=-1000000, high=1000000):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Число вне допустимого диапазона")
    return value


def efficiency(modifier: float, rules: dict) -> float:
    knots = rules["modifier_scale"]
    modifier = finite(modifier, -100, 1000)
    if modifier <= knots[0][0]:
        return knots[0][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if modifier <= x1:
            return y0 + (y1 - y0) * (modifier - x0) / (x1 - x0)
    return min(1.0, knots[-1][1])


def validate_rules(r):
    # Reject invalid reloads before they can affect a running campaign.
    if r["capacity"]["food"] != 100 or r["morale"]["curve"] <= 1:
        raise ValueError("Провиант ограничен 100%; кривизна морали должна быть >1")
    for family in ("weekly", "yield_high", "prices", "capacity"):
        for value in r[family].values():
            finite(value, 0, 1000000)
    if any(v <= 0 for v in r["prices"].values()):
        raise ValueError("Цены ресурсов должны быть положительными")
    for key in ("food", "components", "materials", "morale", "visibility", "gold"):
        finite(r["initial"][key], 0, 1000000)
    for policy in ("none", "moderate", "increased"):
        for key in ("food", "components", "materials"):
            finite(r["buy"][policy][key], 0, 1000000)
    for key in ACTIONS:
        finite(r["yield_high"][key], 0, 1000000)
    knots = r["modifier_scale"]
    if len(knots) < 2 or any(knots[i][0] >= knots[i+1][0] or knots[i][1] > knots[i+1][1]
                            for i in range(len(knots)-1)):
        raise ValueError("Шкала модификаторов должна возрастать")
    for x, y in knots:
        finite(x, -100, 1000)
        finite(y, 0, 1)
    tiers = r["production"].get("tiers", PRODUCTION_TIERS)
    if not tiers or tiers[0]["minimum"] != 0:
        raise ValueError("Шкала производства должна начинаться с 0% компонентов")
    previous = -1
    for tier in tiers:
        minimum = finite(tier["minimum"], 0, 100)
        if minimum <= previous:
            raise ValueError("Пороги производства должны возрастать")
        previous = minimum
        finite(tier["days"], 1, 365)
        if not isinstance(tier["quantity"], int) or not 1 <= tier["quantity"] <= 100:
            raise ValueError("Количество предметов должно быть целым от 1 до 100")
    level = r["production"].get("item_level", 1)
    if not any(x["level"] == level for x in r["items"]):
        raise ValueError(f"Пустой каталог предметов уровня {level}")
    finite(r["ingredient_basic"], 0, 10000)
    finite(r["ingredient_special"], 0, 10000)
    finite(r["meal_morale"], 0, 100)
    for life in r["lifestyles"].values():
        for key in ("cost", "morale", "visibility"):
            finite(life[key], 0, 10000)
    finite(r["morale"]["exponent"], .01, 1)
    finite(r["morale"]["comfortable_days"], 1, 365)
    finite(r["morale"]["luxury_days"], 1, 365)
    finite(r["noise_per_worker"], 0, 100)
    finite(r["building_upkeep_default"], 0, 100000)


def initial_state(rules):
    chars = {}
    for seed in rules["npc_seed"]:
        c = {"id": ident(), "name": seed["name"], "owner": None, "approved": True,
             "npc": True, "actions": seed["actions"], "skills": {}, "imported_skills": {},
             "time_budget": seed.get("time_budget", 1)}
        # Migration of existing sheet ratings to equivalent modifier anchors.
        for action, (rank, amount) in seed["skills"].items():
            c["skills"][action] = {"rank": {"trained": 1, "expert": 2}[rank],
                                    "modifier": {"low": 5, "medium": 10, "high": 15}[amount]}
        chars[c["id"]] = c
    return {"schema": 1, "camp": {**copy.deepcopy(rules["initial"]),
            "residents": max(rules["residents"], len(chars)),
            "extra_residents": max(0, rules["residents"] - len(chars)),
            "horses": rules["horses"], "location": "Не указано",
            "policies": dict.fromkeys(("food", "components", "materials"), "moderate"),
            "lifestyle": "simple", "day": 0, "buildings": [], "production_progress": 0},
            "characters": chars, "users": {}, "contributions": [], "sessions": [],
            "events": [], "outbox": [], "operations": {}, "revision": 0}


def active_characters(state):
    return [c for c in state["characters"].values()
            if c["approved"]]


def sync_population(state):
    camp = state["camp"]
    named = len(active_characters(state))
    # Old saves store a total, so preserve only its unrepresented residents.
    camp.setdefault("extra_residents", max(0, camp["residents"] - named))
    total = named + camp["extra_residents"]
    if total != camp["residents"]:
        camp["residents"] = total
        state["revision"] += 1
    camp["visibility"] = max(min(100, total), camp["visibility"])


def allocations(character):
    actions = {k: v for k, v in character["actions"].items() if v > 0 and k in character["skills"]}
    total = sum(actions.values())
    return {k: v / total * character.get("time_budget", 1) for k, v in actions.items()} if total else {}


def workforce(state, rules):
    rates = dict.fromkeys(ACTIONS, 0.0)
    contributions = {}
    noisy = 0
    for c in active_characters(state):
        values = {}
        for action, share in allocations(c).items():
            mod = c["skills"][action].get("modifier")
            if mod is None:
                continue
            amount = rules["yield_high"][action] * efficiency(mod, rules) * share
            rates[action] += amount
            values[action] = amount
            if action != "morale":
                noisy += share
        contributions[c["id"]] = values
    return rates, contributions, noisy


def expenses(camp, rules):
    w, scale = rules["weekly"], camp["residents"] / 20
    return {"gold": camp["residents"] * w["living_person"] * rules["lifestyles"][camp["lifestyle"]]["cost"]
                    + camp["horses"] * w["horse"],
            "food": camp["residents"] * w["food_person"], "components": w["components"] * scale,
            "materials": w["materials"] * scale + sum(b["upkeep"] for b in camp["buildings"])}


PRODUCTION_TIERS = [
    {"minimum": 0, "days": 28, "quantity": 1},
    {"minimum": 30, "days": 14, "quantity": 1},
    {"minimum": 50, "days": 10.5, "quantity": 1},
    {"minimum": 70, "days": 7, "quantity": 1},
    {"minimum": 90, "days": 7, "quantity": 2},
]


def production_tier(components, rules):
    tiers = rules["production"].get("tiers", PRODUCTION_TIERS)
    return next(tier for tier in reversed(tiers) if components >= tier["minimum"])


def item_drop(components, rules, rng):
    level = rules["production"].get("item_level", 1)
    catalog = [x for x in rules["items"] if x["level"] == level]
    quantity = production_tier(components, rules)["quantity"]
    return [rng.choice(catalog)["name"] for _ in range(quantity)], ""


def advance(state, days, rules, rng=None):
    """Mutate one transaction, advancing only GAME time; daily steps are chunk invariant."""
    if not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("Укажите от 1 до 365 игровых дней")
    rng = rng or random.Random()
    camp = state["camp"]
    rates, individual, noisy = workforce(state, rules)
    cost = expenses(camp, rules)
    before = {k: camp[k] for k in RESOURCES}
    collected = dict.fromkeys(("gold", "food", "components", "materials"), 0.0)
    last_items, last_note = [], "Тельга ещё не успела подготовить предметы"
    camp.setdefault("production_progress", 0)
    camp.pop("cycle_due", None)
    for _ in range(days):
        camp["day"] += 1
        earned = rates["gold"] / 7
        camp["gold"] += earned
        paid = min(camp["gold"], cost["gold"] / 7)
        camp["gold"] -= paid
        unpaid = 1 - paid / (cost["gold"] / 7) if cost["gold"] else 0
        for resource, production in (("food", rates["hunt"] + rates["cook"]),
                                     ("components", rates["components"]), ("materials", rates["materials"])):
            gain, use = production / 7, cost[resource] / 7
            wanted = rules["buy"][camp["policies"][resource]][resource] * camp["residents"] / 20 / 7
            buy = min(wanted, max(0, rules["capacity"][resource] + use - camp[resource] - gain),
                      camp["gold"] / rules["prices"][resource])
            camp["gold"] = max(0, camp["gold"] - buy * rules["prices"][resource])
            camp[resource] = max(0, min(rules["capacity"][resource], camp[resource] + gain + buy - use))
            collected[resource] += gain
        collected["gold"] += earned
        previous = camp["morale"]
        mr, life = rules["morale"], rules["lifestyles"][camp["lifestyle"]]
        decay = rules["weekly"]["morale_decay"]
        factor = mr["comfortable_days"] / mr["luxury_days"] if camp["lifestyle"] == "luxury" else life["morale"]
        pace = ((700 / mr["comfortable_days"] * factor + decay) * (rates["morale"] / 4) ** mr["exponent"]
                if rates["morale"] else 0) - decay - mr["unpaid_penalty"] * unpaid
        if camp["food"] <= 1e-9:
            pace = -max(mr["hunger_penalty"], abs(pace))
        asym = 100 * mr["curve"]
        z = -100 * math.log(1 - previous / asym) / math.log(asym / (asym - 100))
        z = max(0, min(100, z + pace / 7))
        camp["morale"] = round(max(0, min(100, asym * (1 - math.exp(-math.log(asym / (asym - 100)) * z / 100)))), 10)
        camp["visibility"] = max(min(100, camp["residents"]), min(100, camp["visibility"]
                + (life["visibility"] + noisy * rules["noise_per_worker"] - rates["hide"]) / 7))
        for cid, amounts in individual.items():
            credited = {"gold": amounts.get("gold", 0) / 7,
                        "food": (amounts.get("hunt", 0) + amounts.get("cook", 0)) / 7,
                        "components": amounts.get("components", 0) / 7,
                        "materials": amounts.get("materials", 0) / 7,
                        "morale": max(0, camp["morale"] - previous) * amounts.get("morale", 0) / rates["morale"] if rates["morale"] else 0,
                        "visibility": amounts.get("hide", 0) / 7}
            if any(credited.values()):
                state["contributions"].append({"day": camp["day"], "character": cid, "values": credited})
        tier = production_tier(camp["components"], rules)
        camp["production_progress"] += 1 / tier["days"]
        if camp["production_progress"] >= 1 - 1e-9:
            camp["production_progress"] = max(0, camp["production_progress"] - 1)
            # Only the latest completed batch in this period remains for the players.
            last_items, last_note = item_drop(camp["components"], rules, rng)
    return {"days": days, "day": camp["day"], "before": before,
            "after": {k: camp[k] for k in RESOURCES}, "collected": collected,
            "items": last_items, "item_note": last_note}
