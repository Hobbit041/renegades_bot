from __future__ import annotations

from bot.domain import ACTIONS, RESOURCES, RANKS, active_characters, allocations, expenses
from aiogram.types import MessageEntity

DIVIDER = "━━━━━━━━━━━━━━━"
RESOURCE_ICONS = {"gold": "🪙", "food": "🍲", "components": "🧪", "materials": "🪵", "morale": "🔥", "visibility": "👁️"}
ACTION_ICONS = {"hunt": "🏹", "cook": "🍳", "components": "🧪", "materials": "🪵", "morale": "🎶", "hide": "🌿", "gold": "🪙"}


class RichText(str):
    def __new__(cls, text, entities=()):
        obj = super().__new__(cls, text)
        obj.entities = [e if isinstance(e, MessageEntity) else MessageEntity.model_validate(e) for e in entities]
        return obj

    def __add__(self, other):
        offset = len(self.encode("utf-16-le")) // 2
        entities = self.entities + [e.model_copy(update={"offset": e.offset + offset}) for e in getattr(other, "entities", [])]
        return RichText(str(self) + str(other), entities)

    def __radd__(self, other):
        return RichText(str(other)) + self


def styled(text):
    """Explicit Telegram entities: user names and JSON prose never become markup."""
    entities, offset = [], 0
    headings = ("📜", "🏕️", "⚒️", "📚", "📦", "📉", "🎁", "🏆", "📊", "🎲 Начало")
    for line in str(text).splitlines(keepends=True):
        clean = line.rstrip("\r\n")
        start = None
        if clean.startswith(headings) or clean.startswith("👤 ") and ": " not in clean:
            start = 0
        elif clean.startswith(tuple(RESOURCE_ICONS.values()) + ("👤", "🎯", "✅", "❌", "💥", "📍", "👥", "📅", "🛏️", "⏳")) and ": " in clean:
            start = clean.index(": ") + 2
        if start is not None and clean[start:]:
            entities.append(MessageEntity(type="bold", offset=offset + len(clean[:start].encode("utf-16-le"))//2,
                                           length=len(clean[start:].encode("utf-16-le"))//2))
        offset += len(line.encode("utf-16-le")) // 2
    return RichText(str(text), entities)


def rich_chunks(text, entities=None):
    offset = 0
    entities = getattr(text, "entities", []) if entities is None else entities
    entities = [e if isinstance(e, MessageEntity) else MessageEntity.model_validate(e) for e in entities]
    for part in split_text(str(text)):
        size = len(part.encode("utf-16-le")) // 2
        clipped = []
        for entity in entities:
            start, end = max(offset, entity.offset), min(offset+size, entity.offset+entity.length)
            if start < end:
                clipped.append(entity.model_copy(update={"offset": start-offset, "length": end-start}))
        yield {"text": part, "entities": clipped, "parse_mode": None}
        offset += size


def import_feedback(missing, master=False):
    if not master:
        return ("✅ Персонаж отправлен мастеру. Он проверит данные и назначит доступные занятия."
                + ("\nℹ️ Часть модификаторов в файле отсутствует — мастер сможет уточнить их." if missing else ""))
    return ("✅ Навыки прочитаны. Назначьте каждому занятию подходящий импортированный навык."
            + ("\nВ исходном JSON нет итоговых модификаторов для: " + ", ".join(missing)
               + ". Используйте макрос экспорта навыков или введите их вручную." if missing else ""))


def event_result_text(title, character, choice, outcome, story, changes, check=None, camp=None):
    badge, label = {"success": ("✅", "успех"), "failure": ("❌", "провал"),
                    "critical_failure": ("💥", "критический провал")}[outcome]
    effects = []
    for key, value in changes.items():
        if abs(value) <= 1e-9:
            continue
        suffix = " п.п." if key in ("food", "morale", "visibility") else unit(key)
        sign = "+" if value > 0 else "−"
        effects.append(f"{RESOURCE_ICONS[key]} {RESOURCES[key]}: {sign}{n(abs(value))}{suffix}")
    text = (f"📜 Событие лагеря: {title}\n👤 Персонаж: {character}\n🎯 Действие: {choice}\n"
            f"{badge} Результат: {label}\n{DIVIDER}\n{story}\n\n"
            "📦 Изменения в лагере\n" + ("\n".join(effects) or "Без изменения ресурсов."))
    if camp is not None:
        text += "\n\n🏕️ Состояние лагеря сейчас\n" + balances(camp)
    if check:
        text += f"\n\n🎲 Проверка: {check}"
    return styled(text)


def camp_change_text(description, before, after, title="📜 Новое событие!"):
    lines = []
    current = []
    for key, label in RESOURCES.items():
        value = after[key] - before[key]
        if abs(value) <= 1e-9:
            continue
        sign = "+" if value > 0 else "−"
        suffix = " п.п." if key in ("food", "morale", "visibility") else unit(key)
        lines.append(f"{RESOURCE_ICONS[key]} {label}: {sign}{n(abs(value))}{suffix}")
        current.append(f"{RESOURCE_ICONS[key]} {label}: {n(after[key])}{unit(key)}")
    return styled(f"{title}\n{DIVIDER}\n{description}\n\n📦 Изменения\n"
            + ("\n".join(lines) or "Без изменения ресурсов.")
            + ("\n\n🏕️ Состояние лагеря сейчас\n" + "\n".join(current) if current else ""))


def split_text(text, limit=3600):
    """Telegram measures lengths in UTF-16 units; don't split a non-BMP character."""
    part, size = [], 0
    for ch in text:
        width = 2 if ord(ch) > 0xFFFF else 1
        if size + width > limit:
            yield "".join(part)
            part, size = [], 0
        part.append(ch)
        size += width
    if part:
        yield "".join(part)


def unit(resource):
    return "%" if resource in ("food", "morale", "visibility") else " зм" if resource == "gold" else ""


def n(value):
    return f"{value:.2f}".rstrip("0").rstrip(".")


def balances(camp):
    return styled("\n".join(f"{RESOURCE_ICONS[key]} {name}: {n(camp[key])}{unit(key)}" for key, name in RESOURCES.items()))


def jobs_text(c):
    return ", ".join(f"{ACTIONS[k]} ({n(v*100)}% времени)" for k, v in allocations(c).items()) or "Отдых"


def who_text(state):
    residents = sorted(active_characters(state), key=lambda c: c["name"].casefold())
    lines = [f"👤 {c['name']}: {jobs_text(c)}" for c in residents]
    return styled("⚒️ Кто чем сейчас занят\n" + DIVIDER + "\n"
                  + ("\n".join(lines) or "Пока никто не выбрал занятия."))


def character_text(c, show_modifiers=False):
    known = "\n".join(f"{ACTION_ICONS[k]} {ACTIONS[k]} — {RANKS[v['rank']]}"
                      + (f" · {v['modifier']:+g}" if show_modifiers else "")
                      for k, v in c["skills"].items() if v.get("modifier") is not None)
    return styled(f"👤 {c['name']}\n{DIVIDER}\n📚 Доступные занятия\n"
            f"{known or '⏳ Мастер ещё не назначил занятия'}\n\n⚒️ Текущее занятие\n{jobs_text(c)}")


def action_text(c):
    lines = [f"{ACTION_ICONS[k]} {ACTIONS[k]} — {n(share*100)}% времени · {RANKS[c['skills'][k]['rank']]}"
             for k, share in allocations(c).items()]
    return styled("⚒️ Текущие выполняемые действия\n" + ("\n".join(lines) or "💤 Отдых")
            + "\n\n📅 Когда мастер начнёт новый игровой день, вы узнаете результаты своего вклада в лагерь. "
              "Вы будете выполнять выбранные действия указанное в процентах количество времени до следующей игры."
              "\n\n💡 Повторное нажатие увеличивает долю выбранного занятия. Сброс очищает весь выбор.")


def camp_text(state, rules):
    c = state["camp"]
    daily = expenses(c, rules)
    costs = "\n".join(f"{RESOURCE_ICONS[k]} {RESOURCES[k]}: {n(v/7)}{' п.п.' if k == 'food' else ' зм' if k == 'gold' else ''}" for k, v in daily.items())
    return styled(f"🏕️ Лагерь\n📍 Расположение: {c['location']}\n👥 Жителей: {c['residents']}\n📅 Игровой день: {c['day']}\n"
            f"🛏️ Уровень жизни: {rules['lifestyles'][c['lifestyle']]['label']}\n{DIVIDER}\n📦 Запасы и состояние\n"
            + balances(c) + f"\n\n📉 Суточный расход\n{costs}"
            + f"\n\n👁️ Неснижаемая заметность: {min(100, c['residents'])}%")


def session_text(result, state):
    c = state["camp"]
    text = (f"🎲 Начало сессии\n⏳ Прошло игровых дней: {result['days']}\n📅 Сейчас день: {result['day']}\n{DIVIDER}\n"
            + "⚒️ Собрано за период\n" + "\n".join(f"{RESOURCE_ICONS[k]} {RESOURCES[k]}: {n(v)}{' п.п.' if k == 'food' else unit(k)}"
                                                     for k, v in result["collected"].items())
            + "\n\n🏕️ Состояние лагеря\n" + balances(c))
    text += "\n\n🎁 Предметы\n" + ("\n".join("• " + item for item in result["items"]) if result["items"] else result["item_note"])
    return styled(text)


def stats_text(state, character_id=None, resource=None):
    # A rolling GAME month, not dates of Telegram messages.
    cutoff = state["camp"]["day"] - 28
    totals = {}
    for row in state["contributions"]:
        if row["day"] <= cutoff or (character_id and row["character"] != character_id):
            continue
        values = totals.setdefault(row["character"], dict.fromkeys(RESOURCES, 0.0))
        for k, v in row["values"].items():
            values[k] += v
    if character_id:
        total = totals.get(character_id, dict.fromkeys(RESOURCES, 0))
        return styled("📊 Вклад за последние 28 игровых дней\n" + "\n".join(f"{RESOURCE_ICONS[k]} {RESOURCES[k]}: {n(v)}" for k, v in total.items()))
    ranked = sorted(totals.items(), key=lambda pair: pair[1].get(resource, 0), reverse=True)
    lines = [f"{i}. {state['characters'].get(cid, {}).get('name', 'Житель')}: {n(v[resource])}"
             for i, (cid, v) in enumerate((x for x in ranked if x[1].get(resource, 0) > 0), 1) if i <= 10]
    return styled(f"🏆 {RESOURCE_ICONS[resource]} {RESOURCES[resource]}\n📅 Последние 28 игровых дней\n{DIVIDER}\n" + ("\n".join(lines) or "Пока нет вкладов"))
