"""Create a readable review copy from the live JSON catalog."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from bot.domain import RESOURCES
from bot.events import SKILLS


def export(catalog):
    lines = ["# События лагеря", "", "Источник: config/events.json. Эта копия предназначена для чтения; бот использует JSON.",
             "", "Во всех событиях можно пройти мимо без последствий. Сложная проверка: −15 процентных пунктов успеха.",
             "Изменения морали, провианта и заметности указаны в п.п. Остальные ресурсы — в единицах лагерной модели.", ""]
    for i, event in enumerate(catalog, 1):
        lines += [f"## {i}. {event['title']}", "", event["text"], ""]
        for choice in event["choices"]:
            skills = " или ".join(SKILLS.get(k, k) for k in choice["skills"])
            difficulty = " — сложная, −15 п.п." if choice.get("penalty") == .15 else ""
            lines += [f"### {choice['label']}", "", f"Проверка: **{skills}{difficulty}**.", ""]
            if choice.get("critical_chance"):
                lines += [f"Критический провал: {choice['critical_chance']*100:g}%; максимум успеха — 93%.", ""]
            for outcome, label in (("success", "Успех"), ("failure", "Провал"), ("critical_failure", "Критический провал")):
                if outcome not in choice:
                    continue
                result = choice[outcome]
                effects = "; ".join(f"{RESOURCES[k]} {v:+g}" for k, v in result.get("effects", {}).items()) or "Без изменения ресурсов"
                lines += [f"**{label}.** {result['text']}", "", f"*{effects}.*", ""]
        lines += [f"**{event.get('skip_label', 'Пройти мимо')}:** событие отменяется, без записи в журнал лагеря.", "", "---", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    data = json.loads((root / "config/events.json").read_text(encoding="utf-8"))
    target = root / "docs/events.md"
    target.parent.mkdir(exist_ok=True)
    target.write_text(export(data), encoding="utf-8")
    print(f"Exported {len(data)} events to {target}")
