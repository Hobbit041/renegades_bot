from bot.presentation import camp_change_text, split_text, stats_text
from bot.service import active_check, undo_active_check

import pytest


def test_utf16_chunking():
    value = "A😀" * 5000
    parts = list(split_text(value))
    assert "".join(parts) == value
    assert all(len(x.encode("utf-16-le")) // 2 <= 3600 for x in parts)


def test_undo_only_before_next_camp_change(state, rules):
    active_check(state, "one", "components", -3, "Ремесло", rules)
    undo_active_check(state)
    assert state["camp"]["components"] == 42
    active_check(state, "two", "components", -3, "Ремесло", rules)
    state["camp"]["day"] += 1
    with pytest.raises(ValueError):
        undo_active_check(state)


def test_game_month_not_real_time(state):
    state["camp"]["day"] = 40
    state["characters"]["c"] = {"name": "Герра"}
    state["contributions"] = [{"day": 12, "character": "c", "values": {"gold": 500}},
                              {"day": 13, "character": "c", "values": {"gold": 5}}]
    assert "Герра: 5" in stats_text(state, resource="gold")
    assert "500" not in stats_text(state, resource="gold")


def test_active_resource_update_is_immediate_and_published_once(state, rules):
    active_check(state, "now", "gold", 10, "Успех вылазки", rules)
    assert state["camp"]["gold"] == 1258
    assert state["camp"]["day"] == 0
    active_check(state, "now", "gold", 10, "Успех вылазки", rules)
    assert len(state["outbox"]) == 1
    assert "🪙 Золото: +10 зм" in state["outbox"][0]["text"]
    assert "🪙 Золото: 1258 зм" in state["outbox"][0]["text"]
    undo_active_check(state)
    assert state["camp"]["gold"] == 1248
    assert len(state["outbox"]) == 2
    assert "🪙 Золото: 1248 зм" in state["outbox"][-1]["text"]


def test_player_card_and_import_feedback_never_list_imported_skills():
    from bot.presentation import character_text, import_feedback
    character = {"name": "Герра <&> 😀", "skills": {"hunt": {"rank": 2, "modifier": 15}},
                 "actions": {"hunt": 1}, "imported_skills": {"secret-lore": {"rank": 4, "modifier": 999}}}
    card = character_text(character)
    assert "Охота — Эксперт" in card and "secret-lore" not in card and "999" not in card
    assert "модификатор" not in card and "<b>" not in card
    assert card.entities
    assert "secret-lore" not in import_feedback(["secret-lore"], master=False)
    assert "secret-lore" in import_feedback(["secret-lore"], master=True)
    assert "Эксперт · +15" in character_text(character, show_modifiers=True)


def test_rich_formatting_survives_json_and_utf16_chunking():
    import json
    from bot.presentation import RichText, rich_chunks
    from bot.service import enqueue
    from aiogram.types import MessageEntity
    text = "😀" * 4000
    rich = RichText(text, [MessageEntity(type="bold", offset=0, length=8000)])
    state = {"outbox": []}
    enqueue(state, "camp", rich)
    row = json.loads(json.dumps(state))["outbox"][0]
    parts = list(rich_chunks(row["text"], row["entities"]))
    assert "".join(p["text"] for p in parts) == text
    assert sum(e.length for p in parts for e in p["entities"]) == 8000
    assert all(p["parse_mode"] is None for p in parts)
    assert all(0 <= e.offset < e.offset+e.length <= len(p["text"].encode('utf-16-le'))//2
               for p in parts for e in p["entities"])
