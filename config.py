"""Настройки экзаменатора. Всё, что зависит от сервера, берётся из окружения."""

import os
from pathlib import Path

BASE_DIR = Path(os.environ.get("THAI_EXAM_DIR", Path(__file__).resolve().parent))
DB_PATH = Path(os.environ.get("THAI_EXAM_DB", BASE_DIR / "exam.db"))
LESSONS_DIR = BASE_DIR / "lessons"
PROPISI_PDF = LESSONS_DIR / "propisi.pdf"
PROPISI_CACHE = BASE_DIR / "propisi_cache"

# управляет ботом только владелец; игроков набирает лобби перед каждым квизом
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
# бот работает только в этой группе (0 - в любой группе, пока id не записан); личку игнорирует
GROUP_ID = int(os.environ.get("GROUP_ID", "0"))

ROUNDS = 20
# сколько раундов какого вида в одном квизе (в сумме ROUNDS)
MIX = {"ru_th": 8, "th_ru": 8, "letter": 4}

MODEL = "claude-sonnet-5"          # разбор уроков и вопросы
HOST_MODEL = "gpt-5.6-luna"         # реплики ведущего
