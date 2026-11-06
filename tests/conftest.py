import copy
import json
from pathlib import Path

import pytest


@pytest.fixture
def rules():
    return json.loads((Path(__file__).parent.parent / "config/rules.json").read_text(encoding="utf-8"))


@pytest.fixture
def state(rules):
    from bot.domain import initial_state
    s = initial_state(rules)
    s["characters"] = {}
    return s


def add_worker(state, action, modifier=15, owner=None):
    from bot.domain import ident
    cid = ident()
    state["characters"][cid] = {"id": cid, "name": cid, "owner": owner, "approved": True,
        "npc": owner is None, "actions": {action: 1}, "skills": {action: {"rank": 2, "modifier": modifier}},
        "time_budget": 1, "imported_skills": {}}
    if owner:
        from bot.service import user
        user(state, owner)["character"] = cid
    return cid
