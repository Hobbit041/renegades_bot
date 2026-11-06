"""Read only skill data. Never execute an uploaded Foundry document or its macros."""
from __future__ import annotations

import json

from bot.domain import finite

ALIASES = {"acr": "acrobatics", "arc": "arcana", "ath": "athletics", "cra": "crafting",
           "dec": "deception", "dip": "diplomacy", "itm": "intimidation", "med": "medicine",
           "nat": "nature", "occ": "occultism", "prf": "performance", "rel": "religion",
           "soc": "society", "ste": "stealth", "sur": "survival", "thi": "thievery"}


def parse_actor(payload: bytes):
    if len(payload) > 5_000_000:
        raise ValueError("JSON больше 5 МБ. Экспортируйте только навыки макросом из папки foundry")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Файл не является JSON в UTF-8") from None
    if isinstance(data, list):
        if len(data) != 1:
            raise ValueError("Нужен экспорт одного персонажа")
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("Нужен объект персонажа")
    legacy = data.get("data", {})
    system = data.get("system", legacy.get("data", legacy) if isinstance(legacy, dict) else {})
    if not isinstance(system, dict):
        raise ValueError("Некорректный system в JSON")
    skills = data.get("skills", system.get("skills", {}))
    if isinstance(skills, dict):
        skills = dict(skills)
        attributes = system.get("attributes", {})
        perception = data.get("perception", system.get("perception", attributes.get("perception") if isinstance(attributes, dict) else None))
        if isinstance(perception, dict):
            skills.setdefault("perception", perception)
        for item in data.get("items", []) if isinstance(data.get("items", []), list) else []:
            if not isinstance(item, dict) or item.get("type") != "lore":
                continue
            lore = item.get("system", {})
            if not isinstance(lore, dict):
                continue
            proficient = lore.get("proficient", {})
            rank = proficient.get("value", 0) if isinstance(proficient, dict) else proficient
            key = str(item.get("name", "Знание")).casefold().replace(" ", "-")
            # A source Lore item's mod.value is not necessarily the prepared total.
            stat = {"rank": rank}
            for field in ("totalModifier", "total"):
                if field in lore:
                    stat[field] = lore[field]
            skills.setdefault(key, stat)
    if not isinstance(skills, dict) or not skills:
        raise ValueError("В экспорте нет навыков персонажа PF2e")
    parsed, missing = {}, []
    for key, skill in list(skills.items())[:100]:
        if not isinstance(skill, dict):
            continue
        slug = ALIASES.get(key, key)
        rank = int(finite(skill.get("rank", skill.get("proficient", 0)), 0, 4))
        modifier = next((skill[k] for k in ("totalModifier", "total", "mod", "modifier")
                         if isinstance(skill.get(k), (int, float)) and not isinstance(skill[k], bool)), None)
        parsed[slug] = {"rank": rank, "modifier": finite(modifier, -100, 1000) if modifier is not None else None}
        if modifier is None:
            missing.append(slug)
    if not parsed:
        raise ValueError("Не удалось прочитать навыки")
    # Source exports often contain ranks only. Guessing bonuses loses item/feat modifiers.
    return str(data.get("name", "Персонаж"))[:80], parsed, missing
