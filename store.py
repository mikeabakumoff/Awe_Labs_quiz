"""SQLite: уроки, квизы, вопросы и что кому уже задавали.

Игрок в базе - это его телеграм-id; история вопросов ведётся по id, поэтому смена
имени в телеграме ничего не сбивает.
"""

import json
import sqlite3
import time

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lessons (
    num INTEGER PRIMARY KEY,
    filename TEXT NOT NULL,
    material TEXT NOT NULL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (       -- идущая сессия квиза в чате: от «Начали» до «Конец»
    chat_id INTEGER PRIMARY KEY,
    session INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS session_msgs (   -- все сообщения сессии, их удаляет кнопка «Конец»
    session INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    PRIMARY KEY (session, msg_id)
);
CREATE TABLE IF NOT EXISTS notes (          -- указания преподавателей, пересланные в группу
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    parsed TEXT NOT NULL,            -- JSON {lesson, letters, focus}
    added_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS used (
    player TEXT NOT NULL,            -- телеграм-id строкой
    qkey TEXT NOT NULL,
    used_at REAL NOT NULL,
    PRIMARY KEY (player, qkey)
);
CREATE TABLE IF NOT EXISTS quiz (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    status TEXT NOT NULL,            -- ready (собран, ждёт «Начинаем») | active | done | stopped
    started_at REAL NOT NULL,
    finished_at REAL,
    players TEXT,                    -- JSON [{"id":..., "name":...}] в порядке ходов
    script TEXT                      -- реплики ведущего, JSON
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    quiz_id INTEGER NOT NULL,
    round INTEGER NOT NULL,          -- с 1
    pos INTEGER NOT NULL,            -- порядок хода в раунде
    player_id INTEGER NOT NULL,
    player TEXT NOT NULL,            -- имя на момент квиза
    kind TEXT NOT NULL,              -- ru_th | th_ru | letter
    prompt TEXT NOT NULL,
    answer TEXT NOT NULL,
    qkey TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}',
    msg_id INTEGER,
    shown_at REAL,
    done_at REAL
);
"""


def connect(path=None) -> sqlite3.Connection:
    con = sqlite3.connect(str(path or config.DB_PATH))
    con.row_factory = sqlite3.Row
    # первая версия была на троих с фиксированными именами и ни разу не играна: её таблицы пустые
    qcols = {r["name"] for r in con.execute("PRAGMA table_info(questions)")}
    if qcols and "player_id" not in qcols:
        con.executescript("DROP TABLE IF EXISTS questions; DROP TABLE IF EXISTS quiz; DROP TABLE IF EXISTS players;")
    con.executescript(SCHEMA)
    return con


# ---------- уроки ----------

def save_lesson(con, num: int, filename: str, material: dict):
    with con:
        con.execute("INSERT OR REPLACE INTO lessons (num, filename, material) VALUES (?,?,?)",
                    (num, filename, json.dumps(material, ensure_ascii=False)))


def lessons(con) -> list[dict]:
    rows = con.execute("SELECT num, filename, material FROM lessons ORDER BY num").fetchall()
    return [{"num": r["num"], "filename": r["filename"], **json.loads(r["material"])} for r in rows]


# ---------- сессии квиза (для уборки чата) ----------

def open_session(con, chat_id: int, session: int):
    with con:
        con.execute("INSERT OR REPLACE INTO sessions (chat_id, session) VALUES (?,?)", (chat_id, session))


def current_session(con, chat_id: int) -> int | None:
    row = con.execute("SELECT session FROM sessions WHERE chat_id=?", (chat_id,)).fetchone()
    return row["session"] if row else None


def track(con, chat_id: int, msg_id: int, session: int | None = None):
    """Запомнить сообщение за сессией (по умолчанию - за текущей в этом чате)."""
    session = session or current_session(con, chat_id)
    if session:
        with con:
            con.execute("INSERT OR IGNORE INTO session_msgs (session, chat_id, msg_id) VALUES (?,?,?)",
                        (session, chat_id, msg_id))


def session_msgs(con, session: int) -> list[int]:
    return [r["msg_id"] for r in con.execute("SELECT msg_id FROM session_msgs WHERE session=? ORDER BY msg_id",
                                             (session,))]


def close_session(con, chat_id: int, session: int):
    with con:
        con.execute("DELETE FROM session_msgs WHERE session=?", (session,))
        con.execute("DELETE FROM sessions WHERE chat_id=? AND session=?", (chat_id, session))


# ---------- указания преподавателей ----------

def save_note(con, text: str, parsed: dict):
    with con:
        con.execute("INSERT INTO notes (text, parsed) VALUES (?,?)", (text, json.dumps(parsed, ensure_ascii=False)))


def notes(con) -> list[dict]:
    return [{"text": r["text"], **json.loads(r["parsed"])}
            for r in con.execute("SELECT text, parsed FROM notes ORDER BY id")]


# ---------- история вопросов ----------

def used_keys(con) -> dict[str, dict[str, float]]:
    """{id игрока строкой: {ключ вопроса: когда задан}}"""
    out: dict[str, dict[str, float]] = {}
    for r in con.execute("SELECT player, qkey, used_at FROM used"):
        out.setdefault(r["player"], {})[r["qkey"]] = r["used_at"]
    return out


def mark_used(con, player_id, qkey: str, t: float | None = None):
    with con:
        con.execute("INSERT OR REPLACE INTO used (player, qkey, used_at) VALUES (?,?,?)",
                    (str(player_id), qkey, t or time.time()))
