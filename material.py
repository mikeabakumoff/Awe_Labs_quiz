"""Разбор PDF урока моделью и словарь, по которому проверяются сгенерированные предложения."""

import base64
import json
import re

import config

EXTRACT_PROMPT = """Это урок курса тайского языка для русскоязычных. Извлеки из него материал для экзаменационного квиза.

Важно: текстовый слой этого PDF портит тайский (знак ำ распадается в « า», пропадают ี ั и тоновые знаки). Тайское написание бери с изображения страницы, не из текстового слоя.

Поля:
- lesson: номер урока из заголовка.
- letters: согласные, которые ВВОДЯТСЯ в этом уроке (не блок «Повторение»). tr - транскрипция буквы и слова-названия, как в уроке, например "[мɔ:] [ма:ʹ]"; ru - перевод слова-названия.
- vowels: гласные и тоновые знаки, которые вводятся в этом уроке. kind - "гласная" или "тоновый знак"; гласную пиши с опорной อ (โอ, อื), тоновый знак над อ (อ่).
- words: ВСЕ тайские слова, у которых в уроке есть перевод или смысл которых ясен из переведённых примеров: нумерованный список «Слова», слова из грамматики, из пояснений (например โรงงาน, ดินสอ) и из фраз. Составные выражения (ทำงาน, ที่ไหน) тоже включай отдельными записями. Имена собственные (โตนี่, อีวาน) не включай. Слоги из фонетических упражнений - не слова.
- grammar: каждое грамматическое правило урока: title - короткое название, rule - суть правила в одном-двух предложениях, examples - тайские примеры из урока с переводом.
- phrases: предложения из раздела «Фразы» и готовые примеры предложений из грамматики с переводом на русский.

Транскрипция строго как в уроке: кириллица, двоеточие для долготы, ɔ, тоны знаками ˇ ˆ ʹ `, квадратные скобки. Не выдумывай того, чего в уроке нет."""

_ITEM = lambda props: {
    "type": "array",
    "items": {"type": "object", "properties": {k: {"type": "string"} for k in props},
              "required": list(props), "additionalProperties": False},
}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "lesson": {"type": "integer"},
        "letters": _ITEM(["letter", "tr", "ru"]),
        "vowels": _ITEM(["sign", "kind", "tr", "ru"]),
        "words": _ITEM(["thai", "tr", "ru"]),
        "grammar": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "rule": {"type": "string"},
                               "examples": _ITEM(["thai", "ru"])},
                "required": ["title", "rule", "examples"],
                "additionalProperties": False,
            },
        },
        "phrases": _ITEM(["thai", "ru"]),
    },
    "required": ["lesson", "letters", "vowels", "words", "grammar", "phrases"],
    "additionalProperties": False,
}


def extract(pdf_bytes: bytes, client=None) -> dict:
    """PDF урока -> материал по EXTRACT_SCHEMA. Синхронный вызов, из бота звать через to_thread."""
    import anthropic
    client = client or anthropic.Anthropic()
    with client.messages.stream(
        model=config.MODEL,
        max_tokens=32000,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.b64encode(pdf_bytes).decode()}},
            {"type": "text", "text": EXTRACT_PROMPT},
        ]}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError("модель отказалась разбирать урок")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("ответ модели обрезан по длине")
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    return normalize(data)


NOTE_PROMPT = """Преподаватель курса тайского прислал ученикам сообщение. Извлеки из него то, что пригодится экзаменационному квизу.

- lesson: к какому уроку относится, 0 если не сказано.
- letters: тайские согласные, которые велено учить или прописывать. Если указаны страницы прописей, переведи их в буквы по карте ниже. Если про буквы ничего нет - пустой массив.
- focus: одной-двумя фразами по-русски, на что сделать упор (слова, правила, темы). Пустая строка, если ничего такого нет.

Карта прописей (страница - буква): {pages}

Сообщение преподавателя:
{text}"""

NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "lesson": {"type": "integer"},
        "letters": {"type": "array", "items": {"type": "string"}},
        "focus": {"type": "string"},
    },
    "required": ["lesson", "letters", "focus"],
    "additionalProperties": False,
}


def parse_note(text: str, client=None) -> dict:
    """Указание преподавателя -> {lesson, letters, focus}. Синхронно, из бота звать через to_thread."""
    import anthropic
    import propisi
    client = client or anthropic.Anthropic()
    pages = ", ".join(f"{page} - {ch}" for ch, (_, page) in propisi.PAGES.items())
    msg = client.messages.create(
        model=config.MODEL,
        max_tokens=4000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": NOTE_SCHEMA}},
        messages=[{"role": "user", "content": NOTE_PROMPT.format(pages=pages, text=text)}],
    )
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    data["letters"] = [l.strip() for l in data["letters"] if l.strip() in propisi.PAGES]
    return data


def _norm_thai(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def normalize(data: dict) -> dict:
    for w in data.get("words", []):
        w["thai"] = _norm_thai(w["thai"])
    for l in data.get("letters", []):
        l["letter"] = l["letter"].strip()
    return data


def guess_lesson(filename: str) -> int | None:
    m = re.search(r"(\d+)", filename)
    return int(m.group(1)) if m else None


# ---------- словарь ----------

def norm_tr(tr: str) -> str:
    """В уроке 2 транскрипция в /косых/, в остальных в [квадратных] скобках: приводим к скобкам."""
    tr = re.sub(r"/([^/]*)/", r"[\1]", (tr or "").strip())
    return tr if tr.startswith("[") or not tr else f"[{tr}]"


def letter_words(lessons: list[dict]) -> list[dict]:
    """Слова-названия выученных букв (ม - ม้า «лошадь», ฟ - ฟัน «зуб»): их тоже можно ставить в предложения.
    Транскрипция слова - вторые скобки в транскрипции буквы из урока, перевод - из урока."""
    import propisi
    out = []
    for les in lessons:
        for l in les.get("letters", []):
            if l["letter"] not in propisi.PAGES or not l.get("ru"):
                continue
            word = propisi.PAGES[l["letter"]][0].split(".", 1)[1]
            brackets = re.findall(r"\[[^\]]*\]", norm_tr(l.get("tr", "")))
            if len(brackets) >= 2:
                out.append({"thai": word, "tr": brackets[-1], "ru": l["ru"], "lesson": les["num"]})
    return out


def vocabulary(lessons: list[dict]) -> dict[str, dict]:
    """Все слова всех уроков, включая слова-названия выученных букв: тайское написание -> {tr, ru, lesson}."""
    vocab = {}
    for les in lessons:
        for w in les.get("words", []):
            vocab.setdefault(w["thai"], {"tr": norm_tr(w["tr"]), "ru": w["ru"], "lesson": les["num"]})
    for w in letter_words(lessons):
        vocab.setdefault(w["thai"], {"tr": w["tr"], "ru": w["ru"], "lesson": w["lesson"]})
    return vocab


def letters(lessons: list[dict]) -> dict[str, dict]:
    out = {}
    for les in lessons:
        for l in les.get("letters", []):
            out.setdefault(l["letter"], {"tr": l["tr"], "ru": l["ru"], "lesson": les["num"]})
    return out


def check_sentence(words: list[str], thai: str, vocab: dict) -> str | None:
    """None, если предложение собрано только из слов уроков; иначе причина отказа."""
    words = [_norm_thai(w) for w in words]
    if not words:
        return "пустое предложение"
    unknown = [w for w in words if w not in vocab]
    if unknown:
        return "нет в уроках: " + " ".join(unknown)
    if "".join(words) != _norm_thai(thai):
        return "слова не складываются в предложение"
    return None


def material_for_prompt(lessons: list[dict]) -> str:
    """Компактный текст материала всех уроков для генерации вопросов."""
    parts = []
    for les in lessons:
        p = [f"## Урок {les['num']}"]
        p.append("Слова: " + "; ".join(f"{w['thai']} {w['tr']} - {w['ru']}" for w in les.get("words", [])))
        for g in les.get("grammar", []):
            ex = "; ".join(f"{e['thai']} ({e['ru']})" for e in g.get("examples", []))
            p.append(f"Правило «{g['title']}»: {g['rule']}" + (f" Примеры: {ex}" if ex else ""))
        if les.get("phrases"):
            p.append("Фразы: " + "; ".join(f"{f['thai']} ({f['ru']})" for f in les["phrases"]))
        parts.append("\n".join(p))
    lw = letter_words(lessons)
    if lw:   # тоже разрешённые слова: ставь их в предложения, это повторение букв
        parts.append("## Слова-названия выученных букв (тоже можно и нужно использовать в предложениях)\n"
                     + "; ".join(f"{w['thai']} {w['tr']} - {w['ru']}" for w in lw))
    return "\n\n".join(parts)
