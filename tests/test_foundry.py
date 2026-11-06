import json

import pytest

from bot.foundry import parse_actor


def test_skills_macro_import():
    name, skills, missing = parse_actor(json.dumps({"name": "Герра", "skills": {"survival": {"rank": 2, "totalModifier": 15}}}).encode())
    assert name == "Герра" and skills["survival"] == {"rank": 2, "modifier": 15}
    assert not missing


def test_source_export_does_not_invent_missing_modifier():
    _, skills, missing = parse_actor(b'{"name":"PC","system":{"skills":{"sur":{"rank":2}}}}')
    assert skills["survival"]["modifier"] is None
    assert missing == ["survival"]


def test_perception_and_lore_import():
    _, skills, missing = parse_actor(json.dumps({"name": "PC", "skills": {"hunting-lore": {"rank": 2, "totalModifier": 14}},
                    "perception": {"rank": 1, "mod": 12}}).encode())
    assert skills["perception"]["modifier"] == 12
    assert skills["hunting-lore"]["modifier"] == 14
    assert not missing


def test_raw_lore_does_not_treat_base_modifier_as_prepared_total():
    data = {"name": "PC", "system": {"skills": {"survival": {"rank": 1}}},
            "items": [{"type": "lore", "name": "Hunting Lore", "system": {"proficient": {"value": 2}, "mod": {"value": 4}}}]}
    _, skills, missing = parse_actor(json.dumps(data).encode())
    assert skills["hunting-lore"] == {"rank": 2, "modifier": None}
    assert "hunting-lore" in missing


@pytest.mark.parametrize("data", [b"oops", b"[]", b"{}", b'{"skills":{"x":{"rank":999}}}'])
def test_bad_import(data):
    with pytest.raises(ValueError):
        parse_actor(data)
