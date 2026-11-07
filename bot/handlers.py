from __future__ import annotations

import io
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from bot.config import load_json
from bot.domain import ACTIONS, POLICIES, RANKS, RESOURCES, active_characters, allocations, finite, ident, validate_rules
from bot.foundry import parse_actor
from bot.events import SKILLS, resolve_event
from bot.presentation import RichText, action_text, balances, camp_change_text, camp_text, character_text, import_feedback, jobs_text, n, rich_chunks, session_text, split_text, stats_text, styled, who_text
from bot.service import active_check, create_character, enqueue, owned, publish_active_check, set_action, start_session, undo_active_check, user


def inline(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows if row])


def home_keyboard(master):
    labels = [["Жители", "Лагерь"], ["Начать сессию", "Управление"]] if master else [["Персонаж", "Лагерь", "Действие"]]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t) for t in row] for row in labels],
                               resize_keyboard=True, is_persistent=True)


async def send_long(message, text, markup=None):
    parts = list(rich_chunks(text))
    for index, part in enumerate(parts):
        await message.answer(**part, reply_markup=markup if index == len(parts)-1 else None)


def make_router(app):
    root = Router()
    router = Router()
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")

    @root.message(Command("camp"), F.chat.type.in_({"group", "supergroup"}),
                  F.chat.id == app.config.chat_id, F.message_thread_id == app.config.thread_id)
    async def topic_camp(message: Message):
        state = await app.store.snapshot()
        for part in rich_chunks(camp_text(state, app.rules)):
            await app.bot.send_message(app.config.chat_id, **part, message_thread_id=app.config.thread_id)

    @root.message(Command("who"), F.chat.type.in_({"group", "supergroup"}),
                  F.chat.id == app.config.chat_id, F.message_thread_id == app.config.thread_id)
    async def topic_who(message: Message):
        state = await app.store.snapshot()
        for part in rich_chunks(who_text(state)):
            await app.bot.send_message(app.config.chat_id, **part, message_thread_id=app.config.thread_id)

    def is_master(uid):
        return uid in app.config.masters

    async def show(message, text, markup=None, edit=False):
        if edit and len(text.encode("utf-16-le")) // 2 <= 3600:
            # Telegram rejects an unchanged edit; avoid an exception for harmless repeat taps.
            entities = getattr(text, "entities", [])
            if message.text == text and (message.entities or []) == entities and message.reply_markup == markup:
                return
            await message.edit_text(str(text), entities=entities, parse_mode=None, reply_markup=markup)
        else:
            await send_long(message, text, markup)

    async def player_screen(message, uid, section, edit=False):
        state = await app.store.snapshot()
        u = state["users"].get(str(uid), {})
        if section == "camp":
            await show(message, camp_text(state, app.rules), inline(
                [("События лагеря", "events")],
                [("Участвовать в событиях: " + ("Да" if u.get("events") else "Нет"), "subscribe")],
                [("Статистика", "stats")]), edit)
            return
        if section == "switch":
            chars = [c for c in state["characters"].values() if c["owner"] == uid]
            await show(message, "Выберите персонажа. В лагере работает только один ваш персонаж одновременно.",
                       inline(*[[(c["name"] + (" ⏳" if not c["approved"] else ""), "switch:" + c["id"])] for c in chars],
                              [("Новый персонаж", "new")], [("Назад", "character")]), edit)
            return
        try:
            c = owned(state, uid)
        except ValueError:
            await show(message, "Выберите или создайте персонажа.", inline([("Смена персонажа", "switch")],
                       [("Новый персонаж", "new")]), edit)
            return
        if section == "action":
            if not c["approved"]:
                await show(message, "Персонаж ожидает решения мастера.", inline([("Назад", "character")]), edit)
                return
            rows = [[(label, f"act:{c['id']}:{key}")] for key, label in ACTIONS.items()
                    if key in c["skills"] and c["skills"][key].get("modifier") is not None]
            await show(message, action_text(c), inline(*rows, [("Отменить выбор", f"act:{c['id']}:reset")],
                       [("Назад", "character")]), edit)
        else:
            await show(message, character_text(c) + ("\n\nОжидает одобрения мастера" if not c["approved"] else ""),
                       inline([("Статистика", "my_stats"), ("Смена персонажа", "switch")], [("Назад", "home")]), edit)

    async def residents(message, edit=False):
        state = await app.store.snapshot()
        rows = [[(("⏳ " if not c["approved"] else "") + c["name"], "resident:" + c["id"])]
                for c in state["characters"].values()]
        await show(message, "Жители: выберите персонажа для изменения навыков, занятий и долей.",
                   inline(*rows, [("Добавить НПС", "npc_new")], [("Назад", "home")]), edit)

    async def resident(message, cid, edit=False):
        state = await app.store.snapshot()
        c = owned(state, message.chat.id, cid, master=True)
        skills = "\n".join(f"• {k}: {RANKS[v['rank']]} / {v.get('modifier', 'не указан')}"
                            for k, v in c["imported_skills"].items())
        await show(message, character_text(c, show_modifiers=True) + f"\n\nДоля рабочего времени: {n(c['time_budget']*100)}%"
                   + ("\n\nИмпортированные навыки:\n" + skills if skills else ""),
                   inline([("Назначить занятия / навыки", "grants:" + cid)],
                          [("Импорт из Foundry", "import:" + cid), ("Работа и доли", "npcwork:" + cid)],
                          [("Навыки событий", "eventskills:" + cid)],
                          [("Имя / доля времени", "char_edit:" + cid)],
                          [("Одобрить" if not c["approved"] else "Одобрен", "approve:" + cid)],
                          [("Назад", "residents")]), edit)

    async def management(message, edit=False):
        await show(message, "Управление лагерем", inline(
            [("Уровень жизни", "life")],
            [("Расходы на ресурсы", "policies")], [("Жители / лошади", "population")],
            [("Расположение / переезд", "move")], [("Постройки", "buildings")],
            [("Активная проверка", "check")], [("Отменить последнюю активную проверку", "undo_check")],
            [("Перечитать баланс JSON", "reload")],
            [("Выгрузить состояние и журнал", "export")], [("Назад", "home")]), edit)

    @router.message(Command("start", "menu", "cancel"))
    async def welcome(message: Message):
        uid = message.from_user.id
        async with app.store.transaction() as state:
            u = user(state, uid)
            u["name"] = "@" + message.from_user.username if message.from_user.username else message.from_user.full_name
            u["dialog"] = None
        await message.answer("Управление лагерем" if is_master(uid) else "Лагерь. Выберите раздел.",
                             reply_markup=home_keyboard(is_master(uid)))

    @router.message(F.text.in_({"Персонаж", "Лагерь", "Действие", "Жители", "Начать сессию", "Управление"}))
    async def navigation(message: Message):
        uid = message.from_user.id
        async with app.store.transaction() as state:
            user(state, uid)["dialog"] = None
        if message.text == "Лагерь":
            await player_screen(message, uid, "camp")
        elif is_master(uid):
            if message.text == "Жители":
                await residents(message)
            elif message.text == "Начать сессию":
                async with app.store.transaction() as state:
                    user(state, uid)["dialog"] = {"kind": "session_days"}
                await message.answer("Сколько игровых дней прошло? Введите целое число от 1 до 365.")
            else:
                await management(message)
        elif message.text in ("Персонаж", "Действие"):
            await player_screen(message, uid, "character" if message.text == "Персонаж" else "action")

    @router.callback_query()
    async def callback(query: CallbackQuery):
        uid, data, message = query.from_user.id, query.data or "", query.message
        await query.answer()
        master = is_master(uid)
        try:
            if data.startswith("evt:"):
                _, instance_id, choice_id = data.split(":")
                async with app.store.transaction() as state:
                    result = resolve_event(state, uid, instance_id, choice_id, app.rules)
                await show(message, RichText(result["text"], result.get("text_entities", [])), None, True)
            elif data in ("home", "character", "action", "camp", "switch"):
                async with app.store.transaction() as state:
                    user(state, uid)["dialog"] = None
                if data == "home":
                    await message.answer("Выберите раздел.", reply_markup=home_keyboard(master))
                else:
                    await player_screen(message, uid, data, True)
            elif data == "new":
                async with app.store.transaction() as state:
                    user(state, uid)["dialog"] = {"kind": "new_character"}
                await message.answer("Введите имя вашего персонажа или отправьте JSON из Foundry. /cancel — отмена.")
            elif data.startswith("switch:"):
                async with app.store.transaction() as state:
                    c = owned(state, uid, data.split(":")[1])
                    u = user(state, uid)
                    u["character"] = c["id"]
                    state["revision"] += 1
                await player_screen(message, uid, "character", True)
            elif data.startswith("act:"):
                _, cid, action = data.split(":")
                async with app.store.transaction() as state:
                    key = "tap:" + query.id
                    if key not in state["operations"]:
                        # Old buttons for an inactive character must not secretly change their work.
                        if not master and user(state, uid)["character"] != cid:
                            raise ValueError("Сначала выберите этого персонажа")
                        set_action(state, uid, action, app.config.debounce, cid, master)
                        state["operations"][key] = True
                if master:
                    await npcwork(message, cid, True)
                else:
                    await player_screen(message, uid, "action", True)
            elif data == "subscribe":
                async with app.store.transaction() as state:
                    u = user(state, uid)
                    u["events"] = not u["events"]
                await player_screen(message, uid, "camp", True)
            elif data == "events":
                state = await app.store.snapshot()
                rows = [session_text(s, {**state, "camp": {**state["camp"], **s["after"]}})
                        for s in state["sessions"][-5:]]
                rows += [f"День {e['day']}: {e['text']}" for e in state["events"][-10:]]
                await show(message, "События лагеря\n\n" + ("\n\n".join(reversed(rows)) or "Пока нет событий"))
            elif data == "my_stats":
                state = await app.store.snapshot()
                c = owned(state, uid)
                await show(message, stats_text(state, c["id"]), inline([("Назад", "character")]), True)
            elif data == "stats":
                await show(message, "Статистика за последние 28 игровых дней",
                           inline(*[[(label, "top:" + key)] for key, label in RESOURCES.items()]), True)
            elif data.startswith("top:"):
                resource = data.split(":")[1]
                if resource not in RESOURCES:
                    raise ValueError("Неизвестный ресурс")
                await show(message, stats_text(await app.store.snapshot(), resource=resource), inline([("Назад", "stats")]), True)
            else:
                if not master:
                    raise ValueError("Это действие доступно только мастеру")
                await master_callback(message, uid, data)
        except (ValueError, KeyError, IndexError) as exc:
            await message.answer(str(exc) if isinstance(exc, ValueError) else "Кнопка устарела. Откройте меню заново.")

    async def npcwork(message, cid, edit=False):
        c = owned(await app.store.snapshot(), message.chat.id, cid, master=True)
        await show(message, action_text(c), inline(*[[(v, f"act:{cid}:{k}")] for k, v in ACTIONS.items() if k in c["skills"]],
                   [("Сброс", f"act:{cid}:reset")], [("Назад", "resident:" + cid)]), edit)

    async def master_callback(message, uid, data):
        if data == "residents":
            await dialog(uid, None)
            await residents(message, True)
        elif data.startswith("resident:"):
            await dialog(uid, None)
            await resident(message, data.split(":")[1], True)
        elif data.startswith("npcwork:"):
            await npcwork(message, data.split(":")[1], True)
        elif data == "npc_new":
            await dialog(uid, {"kind": "new_npc"})
            await message.answer("Введите имя НПС. Затем назначьте ему занятия.")
        elif data.startswith("grants:"):
            cid = data.split(":")[1]
            await show(message, "Выберите занятие. Можно назначить навык из импорта или ввести ранг и модификатор вручную.",
                       inline(*[[(label, f"grant:{cid}:{action}")] for action, label in ACTIONS.items()],
                              [("Назад", "resident:" + cid)]), True)
        elif data.startswith("grant:"):
            _, cid, action = data.split(":")
            c = owned(await app.store.snapshot(), uid, cid, True)
            suggested = app.rules.get("action_skills", {}).get(action, [])
            ordered = sorted(c["imported_skills"].items(), key=lambda pair: (pair[0] not in suggested, pair[0]))
            rows = [[(("★ " if slug in suggested else "") + f"{slug}: {RANKS[v['rank']]} / {v.get('modifier')}", f"skill:{cid}:{action}:{slug}")]
                    for slug, v in ordered if len(slug) < 20]
            await dialog(uid, {"kind": "grant", "cid": cid, "action": action})
            await show(message, f"{ACTIONS[action]}: выберите импортированный навык или введите «ранг модификатор» (например 2 15). "
                       "Ранг: 0–4. «удалить» уберёт занятие.", inline(*rows, [("Назад", "grants:" + cid)]), True)
        elif data.startswith("skill:"):
            _, cid, action, slug = data.split(":")
            async with app.store.transaction() as state:
                c = owned(state, uid, cid, True)
                v = c["imported_skills"][slug]
                if v["modifier"] is None:
                    raise ValueError("В исходном экспорте нет итогового модификатора. Введите ранг и модификатор вручную или используйте макрос экспорта навыков")
                c["skills"][action] = {**v, "source_skill": slug}
                user(state, uid)["dialog"] = None
                state["revision"] += 1
            await resident(message, cid)
        elif data.startswith("approve:"):
            cid = data.split(":")[1]
            async with app.store.transaction() as state:
                c = owned(state, uid, cid, True)
                if not c["skills"]:
                    raise ValueError("Сначала назначьте хотя бы одно занятие")
                if not c["approved"]:
                    c["approved"] = True
                    state["revision"] += 1
                    if c["owner"]:
                        u = user(state, c["owner"])
                        if not u["character"]:
                            u["character"] = cid
                        enqueue(state, c["owner"], f"Мастер одобрил персонажа {c['name']}. Откройте «Персонаж» и выберите занятие.")
            await resident(message, cid)
        elif data.startswith("eventskills:"):
            cid = data.split(":")[1]
            c = owned(await app.store.snapshot(), uid, cid, True)
            from bot.events import character_skills
            values = character_skills(c)
            text = "Навыки для событий:\n" + ("\n".join(f"{SKILLS.get(k, k)}: {RANKS[v['rank']]} / {v.get('modifier')}"
                    for k, v in values.items()) or "Ещё не указаны")
            await dialog(uid, {"kind": "event_skill", "cid": cid})
            await show(message, text + "\n\nВведите «ключ ранг модификатор», например perception 2 15 или hunting-lore 1 10. "
                       "Доступные ключи: " + ", ".join(SKILLS) + ". Можно использовать свой ключ знания (Lore). "
                       "Для удаления ручной поправки: «удалить ключ».", inline([("Назад", "resident:" + cid)]), True)
        elif data.startswith("import:"):
            await dialog(uid, {"kind": "import", "cid": data.split(":")[1]})
            await message.answer("Отправьте JSON персонажа Foundry или camp-skills.json из макроса.")
        elif data.startswith("char_edit:"):
            await dialog(uid, {"kind": "char_edit", "cid": data.split(":")[1]})
            await message.answer("Введите «Имя | доля времени в %», например «Тельга | 100».")
        elif data == "planning":
            await message.answer("Выбор занятий доступен сразу после расчёта сессии.")
        elif data.startswith("run:"):
            operation = data.split(":")[1]
            async with app.store.transaction() as state:
                d = user(state, uid)["dialog"]
                if operation in state["operations"]:
                    result = state["operations"][operation]
                else:
                    if not d or d.get("kind") != "session_confirm" or d.get("operation") != operation:
                        raise ValueError("Подтверждение устарело")
                    if d["revision"] != state["revision"]:
                        raise ValueError("Назначения или настройки изменились. Снова нажмите «Начать сессию», чтобы проверить итог")
                    result = start_session(state, uid, operation, d["days"], app.rules)
                    user(state, uid)["dialog"] = None
            await message.answer(f"Рассчитано {result['days']} игровых дней. Отчёт поставлен в очередь публикации. Уже можно выбрать занятия на следующий период.")
        elif data == "life":
            await show(message, "Выберите уровень жизни", inline(*[[(v["label"], "life:" + k)]
                       for k, v in app.rules["lifestyles"].items()]), True)
        elif data.startswith("life:"):
            key = data.split(":")[1]
            if key not in app.rules["lifestyles"]:
                raise ValueError("Неизвестный уровень жизни")
            async with app.store.transaction() as state:
                state["camp"]["lifestyle"] = key
                state["revision"] += 1
            await message.answer("Уровень жизни изменён.")
        elif data == "policies":
            await show(message, "Выберите ресурс", inline(*[[(RESOURCES[k], "policy:" + k)]
                       for k in ("food", "components", "materials")]), True)
        elif data.startswith("policy:"):
            key = data.split(":")[1]
            await show(message, RESOURCES[key], inline(*[[(label, f"policyset:{key}:{k}")] for k, label in POLICIES.items()]), True)
        elif data.startswith("policyset:"):
            _, key, value = data.split(":")
            if key not in ("food", "components", "materials") or value not in POLICIES:
                raise ValueError("Неизвестная политика")
            async with app.store.transaction() as state:
                state["camp"]["policies"][key] = value
                state["revision"] += 1
            await message.answer("Политика расходов изменена.")
        elif data == "check":
            await show(message, "Активная проверка: мастер вводит фактический расход после результата проверки.",
                       inline([("Расход компонентов", "spend:components"), ("Расход материалов", "spend:materials")],
                              [("Особая трапеза", "meal")], [("Произвольный результат / ресурс", "manual_check")],
                              [("Назад", "manage")]), True)
        elif data.startswith("spend:"):
            resource = data.split(":")[1]
            if resource not in ("components", "materials"):
                raise ValueError("Неизвестный ресурс")
            await dialog(uid, {"kind": "spend", "resource": resource})
            await message.answer(f"Сколько списать? Введите «количество | результат проверки». {RESOURCES[resource]}: "
                                 f"1 пункт = {app.rules['prices'][resource]} зм. Пример: 5 | Успешное создание предмета.")
        elif data == "meal":
            await dialog(uid, {"kind": "meal"})
            await message.answer(f"После успешной проверки готовки введите «обычных на порцию особых на порцию порций». "
                                 f"Обычный ингредиент: {app.rules['ingredient_basic']}; особый: {app.rules['ingredient_special']}. "
                                 f"Бонус лагерю: +{app.rules['meal_morale']} морали один раз, эффекты рецепта — только едокам сессии.")
        elif data in ("population", "move", "manual_check"):
            data = "check" if data == "manual_check" else data
            await dialog(uid, {"kind": data})
            prompt = {"population": "Введите «жители лошади», например 20 2. Игроки уже входят в число жителей.",
                      "move": "Введите новое расположение. Это переезд: стационарные здания разбираются с возвратом 40% вложенных материалов. Переносные сохраняются.",
                      "check": "Укажите результат активной проверки: «ресурс | изменение | описание». Ресурсы: gold, food, components, materials, morale, visibility. Пример: components | -5 | Создание предмета. Списание выполняется после подтверждения.\nОбычный ингредиент: 0,1 компонента; особый: 1. Цена компонента и материала задаётся в JSON."}
            await message.answer(prompt[data])
        elif data.startswith("checkrun:"):
            operation = data.split(":")[1]
            async with app.store.transaction() as state:
                d = user(state, uid)["dialog"]
                if operation not in state["operations"]:
                    if not d or d.get("operation") != operation or d.get("kind") != "check_confirm":
                        raise ValueError("Подтверждение устарело")
                    row = active_check(state, operation, d["resource"], d["value"], d["description"], app.rules,
                                       publish=not d.get("meal"))
                    if d.get("meal"):
                        camp = state["camp"]
                        if camp["food"] > 0:
                            camp["morale"] = min(100, camp["morale"] + app.rules["meal_morale"])
                        # The meal is one atomic event, including morale, so undo remains safe.
                        state["events"][-1]["camp_after"] = dict(camp)
                        publish_active_check(state, row)
                    user(state, uid)["dialog"] = None
            row = state["operations"][operation]
            await send_long(message, camp_change_text(row["text"], row["camp_before"], row["camp_after"]))
        elif data == "undo_check":
            async with app.store.transaction() as state:
                before = dict(state["camp"])
                undo_active_check(state)
            await send_long(message, camp_change_text("Последняя активная проверка отменена.", before,
                            state["camp"], title="↩️ Отмена активной проверки"))
        elif data == "buildings":
            state = await app.store.snapshot()
            text = "Постройки:\n" + ("\n".join(f"{b['name']}: содержание {b['upkeep']} / неделю" for b in state["camp"]["buildings"]) or "Пока нет")
            await show(message, text, inline(*[[("Разобрать: " + b["name"], "demolish:" + b["id"])] for b in state["camp"]["buildings"]],
                       [("Построить", "build")], [("Назад", "manage")]), True)
        elif data == "build":
            await dialog(uid, {"kind": "build"})
            await message.answer("Введите «Название | материалы | золото | содержание/неделю | переносное 0/1». "
                                 "Стоимость и содержание выбирает мастер. Пример: Кухня | 40 | 0 | 2 | 0")
        elif data.startswith("demolish:"):
            bid = data.split(":")[1]
            await show(message, "Разобрать здание? Вернётся 40% материалов, для переносного — 100%.",
                       inline([("Подтвердить демонтаж", "demolishrun:" + bid)], [("Назад", "buildings")]), True)
        elif data.startswith("demolishrun:"):
            bid = data.split(":")[1]
            async with app.store.transaction() as state:
                camp = state["camp"]
                b = next((x for x in camp["buildings"] if x["id"] == bid), None)
                if not b:
                    raise ValueError("Постройка уже разобрана")
                refund = b["materials"] * (1 if b["portable"] else .4)
                camp["materials"] = min(app.rules["capacity"]["materials"], camp["materials"] + refund)
                camp["buildings"].remove(b)
                state["revision"] += 1
                state["events"].append({"day": camp["day"], "text": f"Демонтаж: {b['name']}, возврат до {n(refund)} материалов"})
            await message.answer("Постройка разобрана, возврат учтён с ограничением ёмкости склада.")
        elif data == "reload":
            rules = load_json(app.rules_path)
            validate_rules(rules)
            async with app.store.transaction() as state:
                for k, cap in rules["capacity"].items():
                    state["camp"][k] = min(cap, state["camp"][k])
                state["revision"] += 1
                app.rules = rules
            await message.answer("Баланс перечитан из JSON. Начальные запасы и NPC_seed повторно не применяются.")
        elif data == "export":
            import json
            from aiogram.types import BufferedInputFile
            state = await app.store.snapshot()
            await message.answer_document(BufferedInputFile(json.dumps(state, ensure_ascii=False, indent=2).encode(), filename="camp-backup.json"))
        elif data == "manage":
            await management(message, True)
        else:
            raise ValueError("Кнопка устарела. Откройте меню")

    async def dialog(uid, value):
        async with app.store.transaction() as state:
            user(state, uid)["dialog"] = value

    @router.message(F.document)
    async def upload(message: Message):
        uid = message.from_user.id
        state = await app.store.snapshot()
        d = state["users"].get(str(uid), {}).get("dialog")
        if not d or d["kind"] not in ("new_character", "import"):
            await message.answer("Сначала выберите «Новый персонаж» или импорт в панели жителя.")
            return
        try:
            if (message.document.file_size or 0) > 5_000_000:
                raise ValueError("Максимум 5 МБ")
            buf = io.BytesIO()
            await app.bot.download(message.document, destination=buf)
            name, skills, missing = parse_actor(buf.getvalue())
            async with app.store.transaction() as state:
                if d["kind"] == "import":
                    if not is_master(uid):
                        raise ValueError("Импорт в существующего жителя доступен мастеру")
                    c = owned(state, uid, d["cid"], True)
                    c["imported_skills"] = skills
                    state["revision"] += 1
                else:
                    create_character(state, uid, name, app.config.masters, skills)
                user(state, uid)["dialog"] = None
            await message.answer(import_feedback(missing, is_master(uid)))
        except (ValueError, KeyError) as exc:
            await message.answer(str(exc))

    @router.message(F.text)
    async def text_input(message: Message):
        uid, text = message.from_user.id, message.text.strip()
        reply, markup = None, None
        try:
            async with app.store.transaction() as state:
                u = user(state, uid)
                u["name"] = "@" + message.from_user.username if message.from_user.username else message.from_user.full_name
                d = u["dialog"]
                if not d:
                    reply = "Выберите раздел меню. /start — показать кнопки."
                elif d["kind"] == "new_character":
                    create_character(state, uid, text, app.config.masters)
                    reply, u["dialog"] = "Ваш персонаж отправлен мастеру! Он установит доступные занятия исходя из навыков.", None
                else:
                    if not is_master(uid):
                        raise ValueError("Действие доступно только мастеру")
                    kind = d["kind"]
                    if kind == "new_npc":
                        c = create_character(state, uid, text, ())
                        c.update(owner=None, npc=True)
                        reply = f"НПС {c['name']} создан. Назначьте занятия в «Жители» и одобрите."
                    elif kind == "grant":
                        c = owned(state, uid, d["cid"], True)
                        if text.lower() == "удалить":
                            c["skills"].pop(d["action"], None)
                            c["actions"].pop(d["action"], None)
                        else:
                            rank, modifier = text.split()
                            c["skills"][d["action"]] = {"rank": int(finite(rank, 0, 4)), "modifier": finite(modifier, -100, 1000)}
                        state["revision"] += 1
                        reply = "Занятие обновлено."
                    elif kind == "char_edit":
                        name, percent = text.split("|")
                        c = owned(state, uid, d["cid"], True)
                        if not name.strip() or len(name.strip()) > 80:
                            raise ValueError("Имя: 1–80 символов")
                        c.update(name=name.strip(), time_budget=finite(percent.strip(), 0, 100)/100)
                        state["revision"] += 1
                        reply = "Житель обновлён."
                    elif kind == "event_skill":
                        c = owned(state, uid, d["cid"], True)
                        parts = text.split()
                        values = c.setdefault("event_skills", {})
                        if len(parts) == 2 and parts[0].lower() == "удалить":
                            values.pop(parts[1], None)
                        else:
                            slug, rank, modifier = parts
                            if not 1 <= len(slug) <= 50 or ":" in slug:
                                raise ValueError("Ключ навыка: 1–50 символов без двоеточия")
                            values[slug] = {"rank": int(finite(rank, 0, 4)), "modifier": finite(modifier, -100, 1000)}
                        state["revision"] += 1
                        reply = "Навык для событий обновлён."
                    elif kind == "session_days":
                        days = int(text)
                        if not 1 <= days <= 365:
                            raise ValueError("Укажите от 1 до 365 дней")
                        operation = ident()
                        u["dialog"] = {"kind": "session_confirm", "days": days, "operation": operation, "revision": state["revision"]}
                        roster = "\n".join(f"{c['name']}: {jobs_text(c)}" for c in active_characters(state))
                        reply = f"Рассчитать {days} игровых дней и начать сессию?\n\n{roster}\n\nНазначения фиксируются при подтверждении."
                        markup = inline([("Начать сессию", "run:" + operation)], [("Отмена", "home")])
                    elif kind == "population":
                        residents, horses = map(int, text.split())
                        if not 1 <= residents <= 100 or not 0 <= horses <= 100:
                            raise ValueError("Жители: 1–100, лошади: 0–100")
                        state["camp"].update(residents=residents, horses=horses)
                        state["camp"]["visibility"] = max(state["camp"]["visibility"], residents)
                        state["revision"] += 1
                        reply = "Численность изменена."
                    elif kind == "move":
                        if not 1 <= len(text) <= 200:
                            raise ValueError("Расположение: 1–200 символов")
                        camp = state["camp"]
                        refund = sum(b["materials"]*.4 for b in camp["buildings"] if not b["portable"])
                        camp["buildings"] = [b for b in camp["buildings"] if b["portable"]]
                        camp.update(location=text, visibility=min(100, camp["residents"]),
                                    materials=min(app.rules["capacity"]["materials"], camp["materials"] + refund))
                        state["revision"] += 1
                        state["events"].append({"day": camp["day"], "text": "Переезд: " + text})
                        reply = f"Лагерь переехал. Возврат до {n(refund)} материалов с учётом ёмкости. Переносное имущество сохранено."
                    elif kind == "build":
                        name, mats, gold, upkeep, portable = [v.strip() for v in text.split("|")]
                        mats, gold, upkeep = finite(mats, 0), finite(gold, 0), finite(upkeep, 0)
                        if portable not in ("0", "1") or not name or len(name) > 80:
                            raise ValueError("Проверьте имя и признак переносного имущества 0/1")
                        camp = state["camp"]
                        if camp["materials"] < mats or camp["gold"] < gold:
                            raise ValueError("Недостаточно материалов или золота; ничего не списано")
                        camp["materials"] -= mats
                        camp["gold"] -= gold
                        camp["buildings"].append({"id": ident(), "name": name, "materials": mats, "gold": gold, "upkeep": upkeep, "portable": portable == "1"})
                        state["revision"] += 1
                        state["events"].append({"day": camp["day"], "text": "Построено: " + name})
                        reply = "Постройка добавлена, ресурсы списаны."
                    elif kind in ("check", "spend", "meal"):
                        meal = kind == "meal"
                        if kind == "spend":
                            quantity, description = [v.strip() for v in text.split("|", 1)]
                            resource, value = d["resource"], -finite(quantity.replace(",", "."), 0, 100000)
                        elif meal:
                            ordinary, special, servings = map(int, text.split())
                            if not 0 <= ordinary <= 100 or not 0 <= special <= 100 or not 1 <= servings <= 100:
                                raise ValueError("Ингредиенты: 0–100; порции: 1–100")
                            resource = "components"
                            value = -(ordinary*app.rules["ingredient_basic"]+special*app.rules["ingredient_special"])*servings
                            description = f"Особая трапеза: {servings} порций после успешной проверки готовки"
                        else:
                            resource, value, description = [v.strip() for v in text.split("|", 2)]
                        if resource not in RESOURCES or not 1 <= len(description) <= 300:
                            raise ValueError("Проверьте ресурс и описание")
                        operation, value = ident(), finite(value, -100000, 100000)
                        u["dialog"] = {"kind": "check_confirm", "operation": operation, "resource": resource, "value": value, "description": description, "meal": meal}
                        reply = f"{description}\n{RESOURCES[resource]}: {value:+g}. Подтвердить результат и изменение ресурса?"
                        markup = inline([("Подтвердить", "checkrun:" + operation)], [("Отмена", "home")])
                    else:
                        raise ValueError("Подтвердите кнопкой или отмените через /cancel")
                    if kind not in ("session_days", "check", "spend", "meal"):
                        u["dialog"] = None
            await message.answer(reply, reply_markup=markup)
        except (ValueError, KeyError) as exc:
            await message.answer("Не удалось принять ввод: " + str(exc) + "\nПовторите ввод или /cancel.")

    root.include_router(router)
    return root
