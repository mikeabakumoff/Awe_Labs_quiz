"""Сборка квиза: предложения от модели, буквы из прописей, без повторов для каждого игрока."""

import json
import random

import config
import material
import propisi

SENTENCE_PROMPT = """Ты готовишь устный квиз по тайскому для трёх русскоязычных учеников. Ниже весь материал пройденных уроков.

Составь по {n} предложений каждого из трёх уровней сложности:
1 - два-три слова, самая простая конструкция (кто - что делает);
2 - три-пять слов, одно правило из уроков (определение, место, объект, глагол движения с หา или ตาม);
3 - пять-восемь слов, отрицание ไม่, вопросы ที่ไหน или ไปไหน, конструкция ชื่อ, сочетание нескольких правил.

Жёсткие условия:
- Используй ТОЛЬКО слова из разделов «Слова» уроков и «Слова-названия выученных букв», в точности в том написании. Никаких других слов, даже самых простых.
- Примерно в каждое пятое предложение вставляй слово-название буквы (ม้า, ฟัน, ไก่, เสือ...): так ученики повторяют буквы.
- Используй только грамматику, которая есть в уроках, и тайский порядок слов по их правилам.
- Предложение должно быть осмысленным и естественным, перевод на русский однозначным: по нему ученик должен суметь восстановить тайский вариант.
- В поле words - слова предложения по порядку, каждое ровно как в уроках; thai - те же слова подряд без пробелов.
- Разнообразие: разные подлежащие, глаголы и предметы, слова из всех уроков, а не только из последнего.
- Не повторяй предложения из списка «Уже было» и не делай их почти копии.
- Русский перевод пиши без длинного и среднего тире.
{avoid}
# Материал уроков

{material}"""

SENTENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "enum": [1, 2, 3]},
                    "words": {"type": "array", "items": {"type": "string"}},
                    "thai": {"type": "string"},
                    "ru": {"type": "string"},
                },
                "required": ["level", "words", "thai", "ru"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def llm_sentences(lessons: list[dict], avoid: list[str], n: int, notes: list[dict] | None = None,
                  client=None) -> list[dict]:
    import anthropic
    client = client or anthropic.Anthropic()
    avoid_text = ("\nУже было:\n" + "\n".join(avoid[-400:]) + "\n") if avoid else ""
    focus = [n_["focus"] for n_ in notes or [] if n_.get("focus")]
    if focus:   # упор от преподавателя, но словарь по-прежнему только из уроков
        avoid_text += ("\nУказания преподавателя, учти при выборе тем (слова всё равно только из уроков):\n"
                       + "\n".join(f"- {f}" for f in focus[-10:]) + "\n")
    prompt = SENTENCE_PROMPT.format(n=n, avoid=avoid_text, material=material.material_for_prompt(lessons))
    with client.messages.stream(
        model=config.MODEL,
        max_tokens=64000,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SENTENCE_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"генерация предложений прервана: {msg.stop_reason}")
    return json.loads(next(b.text for b in msg.content if b.type == "text"))["sentences"]


def sentence_key(thai: str) -> str:
    return "s:" + material._norm_thai(thai)


class NotEnough(Exception):
    pass


def plan_kinds(n_rounds: int, with_letters: bool, rng: random.Random) -> list[str]:
    """Виды раундов по порядку: буквы равномерно вразбивку, остальное вперемешку."""
    mix = dict(config.MIX)
    if not with_letters:
        mix["ru_th"] += mix.pop("letter") // 2
        mix["th_ru"] = n_rounds - mix["ru_th"]
    sentence_kinds = ["ru_th"] * mix["ru_th"] + ["th_ru"] * mix["th_ru"]
    rng.shuffle(sentence_kinds)
    n_letters = mix.get("letter", 0)
    letter_at = {round((i + 0.8) * n_rounds / n_letters) - 1 for i in range(n_letters)} if n_letters else set()
    kinds = []
    for i in range(n_rounds):
        kinds.append("letter" if i in letter_at else sentence_kinds.pop())
    return kinds


def _levels_for(n: int) -> list[int]:
    """Сложность растёт по ходу квиза: первая треть - 1, дальше 2 и 3."""
    return [1 + (3 * i) // n for i in range(n)]


def transcription(words: list[str], vocab: dict) -> str:
    """Транскрипция предложения из транскрипций слов урока, как в учебнике."""
    return " ".join(vocab[material._norm_thai(w)]["tr"] for w in words)


def _sentence_q(kind: str, s: dict, vocab: dict) -> dict:
    tr = transcription(s["words"], vocab)
    if kind == "ru_th":
        return {"kind": kind, "prompt": "Скажите по-тайски:", "text": s["ru"],
                "answer": f"{s['thai']}\n{tr}", "qkey": sentence_key(s["thai"])}
    return {"kind": kind, "prompt": "Переведите на русский:", "text": s["thai"],
            "answer": f"{s['ru']}\n{tr}", "qkey": sentence_key(s["thai"])}


def _letter_q(letter: str, info: dict) -> dict:
    name, page = propisi.PAGES[letter]
    ru = info.get("ru") or propisi.MEANING.get(letter, "")
    reading = " ".join(x for x in (info.get("tr"), f"- {ru}" if ru else "") if x)
    return {"kind": "letter", "text": "",
            "prompt": f"Буква на странице {page} прописей: как она читается и что значит её слово-название?",
            "answer": f"{letter} ({name}) {reading}".strip(), "qkey": "letter:" + letter,
            "extra": {"pdf_page": page - 1}}


def build(lessons: list[dict], used: dict[str, dict[str, float]], sentences: list[dict], players: list[str],
          with_letters: bool, rng: random.Random | None = None, n_rounds: int = config.ROUNDS,
          notes: list[dict] | None = None) -> list[list[dict]]:
    """Раунды квиза: rounds[i][pos] - вопрос игроку players[pos] (ключ игрока - id строкой).
    NotEnough, если не хватило предложений."""
    rng = rng or random.Random()
    vocab = material.vocabulary(lessons)
    letter_pool = {k: v for k, v in material.letters(lessons).items() if k in propisi.PAGES}
    # буквы, которые велел учить преподаватель, даже если в уроках их ещё не было (ответ - название по прописям)
    for note in notes or []:
        for l in note.get("letters", []):
            if l in propisi.PAGES:
                letter_pool.setdefault(l, {"tr": "", "ru": ""})
    with_letters = with_letters and len(letter_pool) >= 1
    kinds = plan_kinds(n_rounds, with_letters, rng)

    # пул предложений: только собранные из слов уроков, без дублей
    pool: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    seen = set()
    for s in sentences:
        key = sentence_key(s["thai"])
        if key in seen or s.get("level") not in pool or material.check_sentence(s["words"], s["thai"], vocab):
            continue
        seen.add(key)
        pool[s["level"]].append(s)
    for lst in pool.values():
        rng.shuffle(lst)

    taken: set[str] = set()      # ключи, уже попавшие в этот квиз
    got: dict[str, set[str]] = {}
    sentence_rounds = [i for i, k in enumerate(kinds) if k != "letter"]
    levels = dict(zip(sentence_rounds, _levels_for(len(sentence_rounds))))
    rounds: list[list[dict]] = []
    for i, kind in enumerate(kinds):
        row = []
        for p in players:
            mine = used.get(p, {})
            if kind == "letter":
                # сначала буквы, не занятые в этом квизе и не виденные игроком, потом давно виденные;
                # если игроков больше, чем букв, буква может достаться двоим в одном квизе
                own = got.setdefault(p, set())   # буквы, уже доставшиеся игроку в этом квизе
                cand = [l for l in letter_pool if l not in own] or list(letter_pool)
                cand.sort(key=lambda l: ("letter:" + l in taken, mine.get("letter:" + l, 0), rng.random()))
                own.add(cand[0])
                q = _letter_q(cand[0], letter_pool[cand[0]])
            else:
                lv = levels[i]
                s = None
                for level in sorted(pool, key=lambda x: abs(x - lv)):
                    s = next((s for s in pool[level] if sentence_key(s["thai"]) not in taken
                              and sentence_key(s["thai"]) not in mine), None)
                    if s:
                        break
                if s is None:
                    raise NotEnough(f"раунд {i + 1}, игрок {p}")
                q = _sentence_q(kind, s, vocab)
            taken.add(q["qkey"])
            row.append(q)
        rounds.append(row)
    return rounds


def sentences_per_level(n_players: int, n_rounds: int = config.ROUNDS) -> int:
    """Сколько предложений каждого уровня просить у модели: с запасом на отсев."""
    sentence_rounds = n_rounds - config.MIX.get("letter", 0)
    need = -(-sentence_rounds * n_players // 3)
    return max(12, int(need * 1.5))


def avoid_list(used: dict[str, dict[str, float]]) -> list[str]:
    """Все предложения, которые уже кому-либо задавались."""
    return sorted({k[2:] for keys in used.values() for k in keys if k.startswith("s:")})


def make_quiz(lessons: list[dict], used: dict[str, dict[str, float]], players: list[str], with_letters: bool,
              source=llm_sentences, attempts: int = 3, notes: list[dict] | None = None) -> list[list[dict]]:
    """Просит у модели предложения и собирает квиз; при нехватке просит ещё, копя пул.
    notes - указания преподавателей: упор для предложений и дополнительные буквы прописей."""
    avoid = avoid_list(used)
    sentences: list[dict] = []
    n = sentences_per_level(len(players))
    for _ in range(attempts):
        sentences += source(lessons, avoid + [s["thai"] for s in sentences], n, notes)
        try:
            return build(lessons, used, sentences, players, with_letters, notes=notes)
        except NotEnough:
            n = max(12, n // 2)
    raise NotEnough("модель не дала достаточно новых предложений из слов уроков")
