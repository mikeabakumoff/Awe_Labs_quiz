"""Разбор урока из командной строки, без телеграма: python ingest.py <pdf> [...]"""

import sys
from pathlib import Path

import material
import store


def main(paths: list[str]):
    con = store.connect()
    files = []
    for p in map(Path, paths):   # каталог = все pdf уроков в нём, кроме прописей
        files += sorted(f for f in p.glob("*.pdf") if "propis" not in f.name.lower()) if p.is_dir() else [p]
    for p in files:
        data = material.extract(p.read_bytes())
        num = data.get("lesson") or material.guess_lesson(p.name)
        store.save_lesson(con, num, p.name, data)
        print(f"урок {num}: слов {len(data['words'])}, букв {len(data['letters'])}, "
              f"правил {len(data['grammar'])}, фраз {len(data['phrases'])}  <- {p.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
