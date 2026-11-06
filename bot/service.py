from __future__ import annotations

import copy
import hashlib
import json
import random
import time

from aiogram.types import MessageEntity

from bot.domain import ACTIONS, advance, allocations, finite, ident
from bot.presentation import DIVIDER, RichText, camp_change_text, session_text


def enqueue(state, target, text, key=None, markup=None, event_id=None):
    if key and any(x.get("key") == key for x in state["outbox"]):
        return
    state["outbox"].append({"id": ident(), "target": target, "text": str(text), "key": key,
                            "entities": [e.model_dump(mode="json", exclude_none=True) for e in getattr(text, "entities", [])],
                            "sent": False, "attempts": 0, "retry_at": 0,
                            "markup": markup, "event_id": event_id})


def user(state, uid):
    return state["users"].setdefault(str(uid), {"character": None, "events": False,
                    "name": str(uid), "dialog": None, "announce_due": None,
                    "announced": [], "last_event_date": None})


def owned(state, uid, cid=None, master=False):
    cid = cid or user(state, uid).get("character")
    c = state["characters"].get(cid)
    if not c or (c["owner"] != uid and not master):
        raise ValueError("Этот персонаж вам недоступен")
    return c


def set_action(state, uid, action, debounce, cid=None, master=False):
    c = owned(state, uid, cid, master)
    if not c["approved"]:
        raise ValueError("Персонаж ещё ожидает мастера")
    if action == "reset":
        c["actions"] = {}
    else:
        skill = c["skills"].get(action)
        if action not in ACTIONS or not skill or skill.get("modifier") is None:
            raise ValueError("Мастер не назначил это занятие")
        c["actions"][action] = c["actions"].get(action, 0) + 1
    state["revision"] += 1
    # Masters editing NPCs do not impersonate a player's announcement.
    if c["owner"] is not None:
        user(state, c["owner"])["announce_due"] = time.time() + debounce
    return c


def create_character(state, uid, name, masters, imported=None):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("Имя должно содержать от 1 до 80 символов")
    if sum(c["owner"] == uid for c in state["characters"].values()) >= 20:
        raise ValueError("Лимит: 20 персонажей на пользователя")
    c = {"id": ident(), "name": name, "owner": uid, "approved": False, "npc": False,
         "skills": {}, "imported_skills": imported or {}, "actions": {}, "time_budget": 1}
    state["characters"][c["id"]] = c
    for master in masters:
        enqueue(state, master, f"🆕 Новый персонаж: {name}\n\n👤 Владелец: {user(state, uid)['name']}\n"
                f"Telegram ID: {uid}\nID персонажа: {c['id']}\n\n{DIVIDER}\n\n"
                f"📋 Откройте «Жители» → {name}: проверьте навыки, назначьте занятия и одобрите.")
    return c


def due_announcements(state, now):
    for uid, u in state["users"].items():
        if u["announce_due"] is None or u["announce_due"] > now:
            continue
        u["announce_due"] = None
        cid = u.get("character")
        c = state["characters"].get(cid)
        chosen = sorted(allocations(c)) if c and c["approved"] else []
        # Shares alone, including changes during a debounce window, never post.
        signature = [cid, chosen] if chosen else []
        if signature == u["announced"]:
            continue
        u["announced"] = signature
        who = ", ".join(ACTIONS[k] for k in chosen) or "Отдых"
        hint = "Команда /who покажет деятельность других жителей."
        footer = RichText(hint, [MessageEntity(type="italic", offset=0, length=len(hint))])
        enqueue(state, "camp", f"⚒️ {u['name']} займётся следующими делами в лагере:\n{who}\n\n"
                f"{DIVIDER}\n" + footer)


def start_session(state, uid, operation, days, rules):
    if operation in state["operations"]:
        return state["operations"][operation]
    result = advance(state, days, rules)
    fingerprint = hashlib.sha256(json.dumps(rules, sort_keys=True).encode()).hexdigest()[:16]
    state.setdefault("rule_versions", {})[fingerprint] = copy.deepcopy(rules)
    result["rules_version"] = fingerprint
    state["revision"] += 1
    result["id"], result["master"] = operation, uid
    state["sessions"].append(result)
    state["operations"][operation] = result
    for u in state["users"].values():
        u["announce_due"] = None
    enqueue(state, "camp", session_text(result, state), "session:" + operation)
    return result


def active_check(state, operation, resource, value, description, rules=None, publish=True):
    if operation in state["operations"]:
        return state["operations"][operation]
    if resource not in ("gold", "food", "components", "materials", "morale", "visibility"):
        raise ValueError("Неизвестный ресурс")
    value = finite(value, -100000, 100000)
    camp = state["camp"]
    before = copy.deepcopy(camp)
    if camp[resource] + value < -1e-9:
        raise ValueError("Недостаточно ресурсов: списание отклонено целиком")
    if resource == "visibility":
        value = max(min(100, camp["residents"]), min(100, camp[resource] + value)) - camp[resource]
    elif resource in ("food", "morale"):
        value = min(100, camp[resource] + value) - camp[resource]
    elif rules and resource in rules["capacity"]:
        value = min(rules["capacity"][resource], camp[resource] + value) - camp[resource]
    camp[resource] += value
    row = {"id": operation, "day": camp["day"], "text": description, "resource": resource, "value": value,
           "camp_before": before, "camp_after": copy.deepcopy(camp)}
    state["events"].append(row)
    state["operations"][operation] = row
    state["revision"] += 1
    if publish:
        publish_active_check(state, row)
    return row


def publish_active_check(state, row):
    enqueue(state, "camp", camp_change_text(row["text"], row["camp_before"], row["camp_after"]),
            key="active-check:" + row["id"])


def undo_active_check(state):
    if not state["events"]:
        raise ValueError("Нет активной проверки для отмены")
    row = state["events"][-1]
    if "camp_after" not in row or state["camp"] != row["camp_after"]:
        raise ValueError("После проверки лагерь уже изменился; автоматическая отмена небезопасна. Внесите корректировку активной проверкой")
    before_undo = copy.deepcopy(state["camp"])
    state["camp"] = copy.deepcopy(row["camp_before"])
    state["events"].append({"day": state["camp"]["day"], "text": "Отмена активной проверки: " + row["text"]})
    state["revision"] += 1
    enqueue(state, "camp", camp_change_text(row["text"], before_undo, state["camp"], title="↩️ Отмена активной проверки"),
            key="undo-active-check:" + row["id"])


def draw_events(state, catalog, local_date, rng=None, rules=None, now=None):
    from bot.events import issue_event, offer_text
    rng = rng or random.Random()
    if not catalog:
        return
    for uid, u in state["users"].items():
        if not u["events"] or u["last_event_date"] == local_date:
            continue
        character = state["characters"].get(u.get("character"))
        if not character or not character["approved"] or character["owner"] != int(uid):
            continue
        u["last_event_date"] = local_date
        if rng.random() < .5:
            event = rng.choice(catalog)
            entry, markup = issue_event(state, int(uid), character, event, time.time() if now is None else now, rules)
            enqueue(state, int(uid), offer_text(entry),
                    f"event:{uid}:{local_date}", markup, entry["id"])
