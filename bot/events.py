"""Durable personal encounters; real-time expiry, independent of passive camp jobs."""
from __future__ import annotations

import copy
import random
import time

from bot.domain import RESOURCES, efficiency, finite, ident
from bot.presentation import DIVIDER, event_result_text, n, styled

SKILLS = {
    "acrobatics": "Акробатика", "arcana": "Аркана", "athletics": "Атлетика",
    "crafting": "Ремесло", "deception": "Обман", "diplomacy": "Дипломатия",
    "intimidation": "Запугивание", "medicine": "Медицина", "nature": "Природа",
    "occultism": "Оккультизм", "performance": "Выступление", "religion": "Религия",
    "society": "Общество", "stealth": "Скрытность", "survival": "Выживание",
    "thievery": "Воровство", "perception": "Внимание (Восприятие)",
    "hunting-lore": "Охотничьи знания",
}


def validate_catalog(catalog):
    if not isinstance(catalog, list):
        raise ValueError("Каталог событий должен быть массивом")
    seen = set()
    for event in catalog:
        if not isinstance(event, dict) or not isinstance(event.get("title"), str) or not isinstance(event.get("text"), str):
            raise ValueError("У события нужны title и text")
        eid = event.get("id")
        if not isinstance(eid, str) or not eid or eid in seen:
            raise ValueError("У каждого события должен быть уникальный id")
        seen.add(eid)
        choices = event.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= 5:
            raise ValueError(f"Нужны 1–5 вариантов события {eid}")
        for choice in choices:
            if (not isinstance(choice, dict) or not isinstance(choice.get("label"), str)
                    or not isinstance(choice.get("skills"), list) or not choice["skills"]
                    or not all(isinstance(k, str) for k in choice["skills"])):
                raise ValueError(f"Варианту события {eid} нужны label и skills")
            finite(choice.get("penalty", 0), 0, 1)
            finite(choice.get("critical_chance", 0), 0, 1)
            if choice.get("critical_mode", "reserved") not in ("reserved", "from_failure"):
                raise ValueError("Неизвестный critical_mode")
            for outcome in ("success", "failure", "critical_failure"):
                value = choice.get(outcome, {"text": "", "effects": {}})
                if not isinstance(value, dict) or not isinstance(value.get("text", ""), str):
                    raise ValueError("У исхода нужны text и effects")
                effects = value.get("effects", {})
                if not isinstance(effects, dict):
                    raise ValueError("effects должен быть объектом ресурсов")
                for key, amount in effects.items():
                    if key not in RESOURCES:
                        raise ValueError(f"Неизвестный ресурс {key}")
                    finite(amount, -10000, 10000)


def character_skills(character):
    skills = {}
    # Only a skill explicitly assigned by the master is a valid fallback;
    # a passive job is not proof of its suggested ability (cooking could be Lore).
    for granted in character.get("skills", {}).values():
        source = granted.get("source_skill")
        if source:
            skills[source] = copy.deepcopy(granted)
    skills.update(copy.deepcopy(character.get("imported_skills", {})))
    skills.update(copy.deepcopy(character.get("event_skills", {})))
    return skills


def best_skill(skills, alternatives):
    candidates = []
    for key, stat in skills.items():
        if not isinstance(stat, dict) or stat.get("modifier") is None:
            continue
        normalized = key.casefold().replace("_", "-").replace(" ", "-")
        matches = key in alternatives
        if "hunting-lore" in alternatives:
            matches |= ("hunting" in normalized or "охот" in normalized) and ("lore" in normalized or "знан" in normalized)
        if matches:
            candidates.append((finite(stat["modifier"], -100, 1000), key))
    if not candidates:
        raise ValueError("Нет итогового модификатора для проверки: " + ", ".join(SKILLS.get(k, k) for k in alternatives)
                         + ". Мастер может добавить его в «Навыки событий»; пройти мимо можно всегда.")
    modifier, key = max(candidates)
    return key, modifier


def issue_event(state, uid, character, event, now, rules):
    instance_id = ident()
    entry = {"id": instance_id, "owner": uid, "character": character["id"],
             "character_name": character["name"], "definition": copy.deepcopy(event),
             "skills": character_skills(character), "modifier_scale": copy.deepcopy(rules["modifier_scale"]),
             "issued_at": now, "expires_at": now + 86400, "status": "active"}
    state.setdefault("event_instances", {})[instance_id] = entry
    buttons = [[{"text": c["label"], "callback_data": f"evt:{instance_id}:{i}"}]
               for i, c in enumerate(event["choices"])]
    buttons.append([{"text": event.get("skip_label", "Пройти мимо"), "callback_data": f"evt:{instance_id}:skip"}])
    return entry, {"inline_keyboard": buttons}


def offer_text(entry):
    event = entry["definition"]
    lines = [f"📜 Событие лагеря: {event['title']}", f"👤 Персонаж: {entry['character_name']}",
             DIVIDER, event["text"], "", "🎯 Доступные действия"]
    for choice in event["choices"]:
        names = " или ".join(SKILLS.get(k, k) for k in choice["skills"])
        difficulty = " · ⚠️ сложная, −15%" if choice.get("penalty") == .15 else ""
        lines.append(f"• {choice['label']} — {names}{difficulty}")
    lines.append("\n⏳ Событие действует 24 часа.\n🚶 Пройти мимо можно без последствий.")
    return styled("\n".join(lines))


def resolve_event(state, uid, instance_id, choice_id, rules, now=None, rng=None):
    now, rng = time.time() if now is None else now, rng or random.Random()
    entry = state.setdefault("event_instances", {}).get(instance_id)
    if not entry:
        raise ValueError("Событие уже устарело")
    if entry["owner"] != uid:
        raise ValueError("Это событие другого пользователя")
    if now >= entry["expires_at"]:
        entry["status"] = "expired"
        return {"status": "expired", "text": "Событие уже устарело"}
    if entry["status"] != "active":
        return entry["result"]
    if choice_id == "skip":
        result = {"status": "skipped", "text": "Вы прошли мимо. Событие отменено."}
        entry.update(status="skipped", result=result)
        return result
    try:
        index = int(choice_id)
    except ValueError:
        raise ValueError("Неизвестный вариант события") from None
    if not 0 <= index < len(entry["definition"]["choices"]):
        raise ValueError("Неизвестный вариант события")
    choice = entry["definition"]["choices"][index]
    # Keep known modifiers frozen. A master may fill a missing value after the offer.
    skills = copy.deepcopy(entry["skills"])
    current = state["characters"].get(entry["character"])
    if current:
        for key, value in character_skills(current).items():
            if skills.get(key, {}).get("modifier") is None:
                skills[key] = value
    skill, modifier = best_skill(skills, choice["skills"])
    chance = max(0, min(1, efficiency(modifier, {"modifier_scale": entry["modifier_scale"]}) - choice.get("penalty", 0)))
    critical = choice.get("critical_chance", 0)
    if choice.get("critical_mode", "reserved") == "reserved":
        chance = min(chance, 1 - critical)
    else:
        critical = min(critical, 1 - chance)
    roll = rng.random()
    if roll < chance:
        outcome = "success"
    elif roll < chance + critical:
        outcome = "critical_failure"
    else:
        outcome = "failure"
    selected = choice.get(outcome, choice.get("failure", {"text": "Без последствий.", "effects": {}}))
    changes = {}
    camp = state["camp"]
    for resource, delta in selected.get("effects", {}).items():
        previous = camp[resource]
        minimum = min(100, camp["residents"]) if resource == "visibility" else 0
        maximum = 100 if resource in ("food", "morale", "visibility") else rules["capacity"].get(resource, float("inf"))
        camp[resource] = max(minimum, min(maximum, previous + delta))
        changes[resource] = camp[resource] - previous
    args = (entry['definition']['title'], entry['character_name'], choice['label'], outcome, selected.get('text', ''), changes)
    public_text = event_result_text(*args, camp=camp)
    text = event_result_text(*args, check=f"{SKILLS.get(skill, skill)} ({modifier:+g}) · успех {n(chance*100)}%", camp=camp)
    result = {"status": "resolved", "outcome": outcome, "text": text, "probability": chance,
              "critical_probability": critical, "skill": skill, "modifier": modifier, "roll": roll,
              "effects": changes, "selected_choice": index, "public_text": public_text,
              "text_entities": [e.model_dump(mode="json", exclude_none=True) for e in text.entities]}
    entry.update(status="resolved", result=result, resolved_at=now)
    state["events"].append({"id": instance_id, "kind": "personal_event", "day": camp["day"],
                            "text": text, "character": entry["character"], "result": copy.deepcopy(result)})
    if changes:
        state["contributions"].append({"day": camp["day"], "character": entry["character"],
                                        "values": {k: -v if k == "visibility" else v for k, v in changes.items()}})
    state["revision"] += 1
    from bot.service import enqueue
    enqueue(state, "camp", public_text, key="event-result:" + instance_id)
    return result
