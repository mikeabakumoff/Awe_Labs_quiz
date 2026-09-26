"""Экзаменатор по тайскому: телеграм-часть.

Владелец кидает в группу PDF уроков, бот их разбирает и копит. По слову «Начали»
открывается лобби: кто хочет играть, жмёт «Я играю», владелец жмёт «Поехали».
Квиз из 20 раундов: в каждом раунде по заданию каждому игроку, одинаковых по виду
и сложности. Время считается от показа задания до «Готово», раунд выигрывает
самый быстрый, квиз - наименьшая сумма времени. Реплики ведущего пишет GPT.
"""

import asyncio
import html
import logging
import os
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, TypeHandler, filters)

import config
import game
import generator
import host
import material
import propisi
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("thai_exam")

con = store.connect()
_busy = {"generating": False}
# лобби по чатам: {"msg_id": ..., "players": {user_id: имя}} - порядок нажатий = порядок ходов
lobbies: dict[int, dict] = {}

START_RE = re.compile(r"^\s*начали\W*$", re.I)
STOP_RE = re.compile(r"^\s*стоп\W*$", re.I)


def esc(s) -> str:
    return html.escape(str(s or "").replace("—", "-").replace("–", "-"))


def fmt_time(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f} с"
    m, s = divmod(sec, 60)
    return f"{int(m)} мин {s:04.1f} с"


def is_owner(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == config.OWNER_ID


def allowed_chat(chat) -> bool:
    return (chat is not None and chat.type in ("group", "supergroup")
            and (config.GROUP_ID == 0 or chat.id == config.GROUP_ID))


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Первым делом для любого апдейта: всё, что не из нашей группы, дальше не идёт."""
    chat, user = update.effective_chat, update.effective_user
    log.info("update chat=%s (%s) user=%s", chat.id if chat else None, chat.type if chat else None,
             user.id if user else None)
    if allowed_chat(chat):
        return
    # добавили в чужую группу - выходим сразу
    if chat is not None and chat.type in ("group", "supergroup") and config.GROUP_ID:
        try:
            await context.bot.leave_chat(chat.id)
            log.warning("left foreign chat %s", chat.id)
        except Exception:
            log.exception("leave_chat failed")
    raise ApplicationHandlerStop


def mention(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{esc(name)}</a>'


def script(quiz_id: int) -> host.Script:
    return host.Script.from_json(game.script_json(con, quiz_id))


def body(q: game.Question, total: int) -> str:
    intro = script(q.quiz_id).intro(q.round, q.pos, q.player)
    out = (f"<b>Раунд {q.round}/{total}</b> · отвечает {mention(q.player_id, q.player)}\n"
           f"🎤 <i>{esc(intro)}</i>\n\n{esc(q.prompt)}")
    if q.extra.get("text"):
        out += f"\n<b>{esc(q.extra['text'])}</b>"
    return out


def done_keyboard(q: game.Question) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Готово", callback_data=f"done:{q.id}")]])


async def send_question(bot, chat_id: int, q: game.Question):
    text = body(q, game.total_rounds(con, q.quiz_id))
    if q.kind == "letter":
        png = await asyncio.to_thread(propisi.render, q.extra["pdf_page"])
        with open(png, "rb") as f:
            msg = await bot.send_photo(chat_id, f, caption=text, parse_mode=ParseMode.HTML,
                                       reply_markup=done_keyboard(q))
    else:
        msg = await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=done_keyboard(q))
    game.mark_shown(con, q, msg.message_id)
    store.track(con, chat_id, msg.message_id)


# ---------- кнопка «Готово» ----------

async def on_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    qid = int(query.data.split(":")[1])
    res = game.press(con, qid, query.from_user.id)
    if res.status == "stale":
        await query.answer("Это задание уже закрыто")
        return
    if res.status == "not_yours":
        await query.answer(f"Сейчас отвечает {res.question.player}")
        return

    q = res.question
    await query.answer(fmt_time(q.elapsed))
    text = body(q, res.total_rounds) + f"\n\n⏱ <b>{fmt_time(q.elapsed)}</b>\nОтвет: {esc(q.answer)}"
    try:
        if q.kind == "letter":
            await query.edit_message_caption(text, parse_mode=ParseMode.HTML)
        else:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("edit after done failed")

    chat_id = query.message.chat_id
    if res.round_result:
        winner, t = res.round_result[0]
        if len(res.round_result) > 1:
            lines = [f"🏁 <b>Раунд {q.round}.</b> {esc(script(q.quiz_id).round_comment(winner, fmt_time(t)))}", ""]
            lines += [f"{i}. {esc(p)} - {fmt_time(s)}" for i, (p, s) in enumerate(res.round_result, 1)]
            m = await context.bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML)
            store.track(con, chat_id, m.message_id)
    if res.next:
        await send_question(context.bot, chat_id, res.next)
    elif res.final:
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 <b>Квиз окончен!</b>", ""]
        for i, (p, s) in enumerate(res.final):
            lines.append(f"{medals[i] if i < 3 else '•'} {esc(p)} - {fmt_time(s)}"
                         + (f", раундов выиграно: {res.wins.get(p, 0)}" if len(res.final) > 1 else ""))
        lines += ["", f"<b>{esc(script(q.quiz_id).finale_line(res.final[0][0]))}</b>"]
        m = await context.bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML,
                                           reply_markup=end_keyboard(chat_id, "🏁 Конец"))
        store.track(con, chat_id, m.message_id)


# ---------- лобби ----------

def lobby_text(lobby: dict) -> str:
    names = list(lobby["players"].values())
    lines = ["🎤 <b>Квиз по тайскому! Набираем игроков.</b>",
             "Кто играет, жмите «Я играю» (нажали ещё раз - вышли). Ведущий, когда все соберутся, жмите «Поехали», и я начну готовить вопросы.",
             ""]
    lines += [f"{i}. {esc(n)}" for i, n in enumerate(names, 1)] or ["Пока никого."]
    return "\n".join(lines)


LOBBY_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🙋 Я играю", callback_data="lobby:join"),
                                  InlineKeyboardButton("▶️ Поехали", callback_data="lobby:go")]])


def display_name(user, taken: set[str]) -> str:
    name = (user.first_name or user.username or "Игрок").strip()
    if name in taken and user.last_name:
        name = f"{name} {user.last_name[0]}."
    base, n = name, 2
    while name in taken:
        name, n = f"{base} {n}", n + 1
    return name


async def on_start_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    if _busy["generating"]:
        await update.message.reply_text("Уже готовлю квиз, подождите.")
        return
    if not store.lessons(con):
        await update.message.reply_text("Уроков пока нет, пришлите PDF урока.")
        return
    owner = update.effective_user
    lobby = {"players": {owner.id: display_name(owner, set())}}
    # с этого сообщения всё, что пишется в чат, относится к квизу и уберётся кнопкой «Конец»
    store.open_session(con, update.effective_chat.id, update.message.message_id)
    msg = await update.message.reply_text(lobby_text(lobby), parse_mode=ParseMode.HTML, reply_markup=LOBBY_KB)
    store.track(con, msg.chat_id, msg.message_id)
    lobby["msg_id"] = msg.message_id
    lobbies[update.effective_chat.id] = lobby


async def on_lobby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    lobby = lobbies.get(chat_id)
    if not lobby or lobby["msg_id"] != query.message.message_id:
        await query.answer("Этот набор уже закрыт")
        return
    user = query.from_user

    if query.data == "lobby:join":
        if user.id in lobby["players"]:
            del lobby["players"][user.id]
            await query.answer("Вы вышли из игры")
        else:
            lobby["players"][user.id] = display_name(user, set(lobby["players"].values()))
            await query.answer("Вы в игре!")
        await query.edit_message_text(lobby_text(lobby), parse_mode=ParseMode.HTML, reply_markup=LOBBY_KB)
        return

    # lobby:go
    if user.id != config.OWNER_ID:
        await query.answer("Запускает владелец")
        return
    if not lobby["players"]:
        await query.answer("Нет ни одного игрока")
        return
    if _busy["generating"]:
        await query.answer("Уже готовлю квиз")
        return
    await query.answer("Поехали!")
    del lobbies[chat_id]
    players = [{"id": uid, "name": name} for uid, name in lobby["players"].items()]
    await query.edit_message_text(lobby_text(lobby).split("\n")[0] + "\n\nИграют: "
                                  + ", ".join(esc(p["name"]) for p in players), parse_mode=ParseMode.HTML)
    await prepare_quiz(context.bot, chat_id, players)


async def prepare_quiz(bot, chat_id: int, players: list[dict]):
    """Собирает квиз под выбранных игроков и спрашивает «Начинаем?». Задания пойдут по кнопке."""
    lessons = store.lessons(con)
    _busy["generating"] = True
    status = await bot.send_message(
        chat_id, f"📚 Уроков в базе: {len(lessons)}, игроков: {len(players)}. "
                 f"Готовлю {config.ROUNDS} раундов, это пара минут...")
    store.track(con, chat_id, status.message_id)
    try:
        # вопросы (Claude) и реплики ведущего (GPT) готовятся параллельно
        rounds, host_script = await asyncio.gather(
            asyncio.to_thread(generator.make_quiz, lessons, store.used_keys(con),
                              [str(p["id"]) for p in players], propisi.available(), notes=store.notes(con)),
            asyncio.to_thread(host.quiz_script, [p["name"] for p in players], config.ROUNDS))
    except Exception as e:
        log.exception("quiz generation failed")
        await status.edit_text(f"❌ Не смог собрать квиз: {e}")
        return
    finally:
        _busy["generating"] = False

    qid = game.start(con, chat_id, players, rounds, script=host_script.to_json())
    await status.edit_text(
        f"🎤 <b>{esc(host_script.opening)}</b>\n\n{config.ROUNDS} раундов, порядок ходов: "
        + ", ".join(esc(p["name"]) for p in players)
        + ".\nОтветили - жмите «Готово». Раунд выигрывает самый быстрый, квиз - наименьшее общее время.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Начинаем", callback_data=f"begin:{qid}")]]))


async def on_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != config.OWNER_ID:
        await query.answer("Запускает владелец")
        return
    if not game.begin(con, int(query.data.split(":")[1])):
        await query.answer("Этот квиз уже не актуален")
        return
    await query.answer("Поехали!")
    await query.edit_message_reply_markup(None)
    await send_question(context.bot, query.message.chat_id, game.current(con))


async def on_stop_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    chat_id = update.effective_chat.id
    had_lobby = lobbies.pop(chat_id, None) is not None
    stopped = game.stop(con)
    text = "Квиз остановлен." if stopped else "Набор игроков закрыт." if had_lobby else "Квиз и так не идёт."
    kb = end_keyboard(chat_id, "🧹 Убрать квиз") if store.current_session(con, chat_id) else None
    m = await update.message.reply_text(text, reply_markup=kb)
    store.track(con, chat_id, m.message_id)


def end_keyboard(chat_id: int, label: str) -> InlineKeyboardMarkup | None:
    session = store.current_session(con, chat_id)
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"end:{session}")]]) if session else None


async def on_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """«Конец»: удаляем из чата всё, что было за сессию квиза, вместе с самой кнопкой."""
    query = update.callback_query
    if query.from_user.id != config.OWNER_ID:
        await query.answer("Завершает владелец")
        return
    chat_id = query.message.chat_id
    session = int(query.data.split(":")[1])
    if game.active_quiz(con) and store.current_session(con, chat_id) == session:
        await query.answer("Квиз ещё идёт")
        return
    ids = sorted(set(store.session_msgs(con, session)) | {query.message.message_id})
    await query.answer("Убираю квиз")
    for i in range(0, len(ids), 100):   # телеграм удаляет до 100 за раз и только моложе 48 часов
        try:
            await context.bot.delete_messages(chat_id, ids[i:i + 100])
        except Exception:
            log.exception("delete_messages failed")
    store.close_session(con, chat_id, session)


async def track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любое сообщение в группе во время сессии квиза запоминаем, чтобы «Конец» убрал и его."""
    if update.message:
        store.track(con, update.effective_chat.id, update.message.message_id)


# ---------- уроки ----------

async def on_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    chat_id = update.effective_chat.id
    doc = update.message.document
    name = doc.file_name or "lesson.pdf"
    config.LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    tg_file = await doc.get_file()
    is_propisi = "propis" in name.lower() or "пропис" in name.lower()
    path = config.PROPISI_PDF if is_propisi else config.LESSONS_DIR / name
    await tg_file.download_to_drive(path)

    # файл у нас - убираем его из группы, чтобы там не копились pdf
    note = ""
    try:
        await update.message.delete()
    except Exception:
        log.exception("delete lesson message failed")
        note = "\n(Удалить сообщение не смог: дайте боту в группе право удалять сообщения.)"

    if is_propisi:
        for p in config.PROPISI_CACHE.glob("*.png"):
            p.unlink()
        await context.bot.send_message(chat_id, "📥 Забрал прописи." + note)
        return

    status = await context.bot.send_message(chat_id, f"📥 Забрал урок «{name}». Разбираю, пара минут...{note}")
    try:
        data = await asyncio.to_thread(material.extract, path.read_bytes())
    except Exception as e:
        log.exception("lesson extract failed")
        await status.edit_text(f"❌ Не смог разобрать {name}: {e}")
        return
    num = data.get("lesson") or material.guess_lesson(name)
    if not num:
        await status.edit_text(f"❌ Не понял номер урока в {name}. Переименуйте файл, например «урок 6.pdf».")
        return
    replaced = any(l["num"] == num for l in store.lessons(con))
    store.save_lesson(con, num, name, data)
    all_nums = [l["num"] for l in store.lessons(con)]
    await status.edit_text(
        f"✅ Урок {num} {'обновлён' if replaced else 'сохранён'}: слов {len(data['words'])}, "
        f"новых букв {len(data['letters'])}, правил {len(data['grammar'])}, фраз {len(data['phrases'])}.\n"
        f"В базе уроки: {', '.join(map(str, all_nums))}.")
    if not replaced:
        await context.bot.send_message(chat_id, await asyncio.to_thread(host.lesson_announcement, num, data))


async def on_teacher_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пересланное владельцем сообщение преподавателя: сохраняем, убираем из группы, учитываем в квизе."""
    if not is_owner(update):
        return
    msg = update.message
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return
    chat_id = update.effective_chat.id
    note = ""
    try:
        await msg.delete()
    except Exception:
        log.exception("delete teacher note failed")
        note = "\n(Удалить сообщение не смог: дайте боту право удалять сообщения.)"
    status = await context.bot.send_message(chat_id, "📥 Забрал указание преподавателя." + note)
    try:
        parsed = await asyncio.to_thread(material.parse_note, text)
    except Exception:
        log.exception("teacher note parse failed")
        parsed = {"lesson": 0, "letters": [], "focus": text[:300]}
    store.save_note(con, text, parsed)
    got = []
    if parsed.get("letters"):
        got.append("буквы для прописей: " + " ".join(parsed["letters"]))
    if parsed.get("focus"):
        got.append("упор: " + parsed["focus"])
    await status.edit_text("📥 Забрал указание преподавателя." + note
                           + ("\nУчту в квизе: " + "; ".join(got) if got else ""))


async def cmd_lessons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    lessons = store.lessons(con)
    if not lessons:
        await update.message.reply_text("Уроков пока нет.")
        return
    lines = [f"Урок {l['num']}: слов {len(l['words'])}, букв {len(l['letters'])}, правил {len(l['grammar'])}"
             for l in lessons]
    lines.append("Прописи: " + ("есть" if propisi.available() else "нет, пришлите PDF с «propisi» в имени"))
    lines.append(f"Указаний преподавателей: {len(store.notes(con))}")
    await update.message.reply_text("\n".join(lines))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "Экзаменатор по тайскому.\n\n"
        "Владелец присылает PDF уроков, бот их копит.\n"
        "«Начали» - набор игроков и новый квиз (запускает владелец), «Стоп» - остановить.\n"
        "/lessons - уроки в базе")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("update failed", exc_info=context.error)


def main():
    app = Application.builder().token(os.environ["THAI_EXAM_BOT_TOKEN"]).build()
    app.add_handler(TypeHandler(Update, gate), group=-1)   # только наша группа
    app.add_handler(MessageHandler(filters.Document.PDF | filters.Document.FileExtension("pdf"), on_pdf))
    # пересланный текст или подпись (указание преподавателя) - раньше «Начали»/«Стоп»
    app.add_handler(MessageHandler(filters.FORWARDED & (filters.TEXT | filters.CAPTION), on_teacher_note))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(START_RE), on_start_quiz))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(STOP_RE), on_stop_quiz))
    app.add_handler(CallbackQueryHandler(on_done, pattern=r"^done:\d+$"))
    app.add_handler(CallbackQueryHandler(on_lobby, pattern=r"^lobby:(join|go)$"))
    app.add_handler(CallbackQueryHandler(on_begin, pattern=r"^begin:\d+$"))
    app.add_handler(CallbackQueryHandler(on_end, pattern=r"^end:\d+$"))
    app.add_handler(MessageHandler(filters.ALL, track_chat), group=1)   # отдельная группа: не мешает командам
    app.add_handler(CommandHandler("lessons", cmd_lessons))
    app.add_handler(CommandHandler(["help", "start"], cmd_help))
    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
