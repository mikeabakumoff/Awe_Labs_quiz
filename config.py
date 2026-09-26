import os
from pathlib import Path

BASE_DIR = Path(os.environ.get("THAI_EXAM_DIR", Path(__file__).resolve().parent))
DB_PATH = Path(os.environ.get("THAI_EXAM_DB", BASE_DIR / "exam.db"))
LESSONS_DIR = BASE_DIR / "lessons"
PROPISI_PDF = LESSONS_DIR / "propisi.pdf"
PROPISI_CACHE = BASE_DIR / "propisi_cache"


OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

GROUP_ID = int(os.environ.get("GROUP_ID", "0"))

ROUNDS = 20

MIX = {"ru_th": 8, "th_ru": 8, "letter": 4}

MODEL = "claude-sonnet-5"
HOST_MODEL = "gpt-5.6-luna"
