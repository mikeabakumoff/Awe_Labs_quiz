"""Проверки логики квиза без телеграма и без моделей. Запуск: python tests/test_exam.py"""

import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
os.environ["THAI_EXAM_DIR"] = tempfile.mkdtemp(prefix="thai-exam-test-")

import config  # noqa: E402
import game  # noqa: E402
import generator  # noqa: E402
import host  # noqa: E402
import material  # noqa: E402
import store  # noqa: E402

NAMES = ["Анна", "Борис", "Вера"]


def players(n: int = 3) -> list[dict]:
    return [{"id": 100 + i, "name": NAMES[i] if i < len(NAMES) else f"Игрок {i + 1}"} for i in range(n)]


def keys(pl: list[dict]) -> list[str]:
    return [str(p["id"]) for p in pl]


PL = players(3)
P = keys(PL)

WORDS = ["ยาย", "ยา", "มา", "มี", "นอน", "รอ", "ไป", "กิน", "ดู", "ตา", "หมอ", "หมา", "ชา", "ไม่", "ที่ไหน",
         "ทำงาน", "หา", "ตาม", "ปู", "ทีวี", "ใน", "โรงหนัง", "พี่ชาย", "พี่สาว", "ผม", "ฉัน", "งู", "ซอย"]
LESSONS = [
    {"num": 1, "words": [{"thai": w, "tr": f"[{w}]", "ru": f"ru-{w}"} for w in WORDS[:14]],
     "letters": [{"letter": c, "tr": f"[{c}]", "ru": f"ru-{c}"} for c in "มนยวรลง"], "grammar": [], "phrases": []},
    {"num": 2, "words": [{"thai": w, "tr": f"[{w}]", "ru": f"ru-{w}"} for w in WORDS[14:]],
     "letters": [{"letter": c, "tr": f"[{c}]", "ru": f"ru-{c}"} for c in "คพฟทฮชซ"], "grammar": [], "phrases": []},
]


def fake_sentences(n_per_level: int, seed: int = 0, bad: int = 0) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for level, size in ((1, 2), (2, 4), (3, 6)):
        for _ in range(n_per_level):
            words = rng.sample(WORDS, size)
            out.append({"level": level, "words": words, "thai": "".join(words), "ru": " ".join(words)})
    for _ in range(bad):   # слово не из уроков
        out.append({"level": 1, "words": ["แม่", "มา"], "thai": "แม่มา", "ru": "мама пришла"})
    return out


def fresh_db():
    return store.connect(Path(tempfile.mkdtemp()) / "t.db")


def start(con, *args, **kw):
    """Собрать квиз и сразу нажать «Начинаем»."""
    qid = game.start(con, *args, **kw)
    assert game.begin(con, qid)
    return qid


def build(sentences, pl_keys=P, used=None, with_letters=True, seed=0):
    return generator.build(LESSONS, used or {}, sentences, pl_keys, with_letters, rng=random.Random(seed))


checks = []


def check(fn):
    checks.append(fn)
    return fn


# ---------- словарь ----------

@check
def sentence_only_from_lesson_words():
    vocab = material.vocabulary(LESSONS)
    assert material.check_sentence(["ยาย", "มา"], "ยายมา", vocab) is None
    assert "แม่" in material.check_sentence(["แม่", "มา"], "แม่มา", vocab)
    assert material.check_sentence(["ยาย", "มา"], "ยายไป", vocab) is not None
    assert material.check_sentence(["ยาย", "มา"], "ยาย มา", vocab) is None   # пробелы не мешают


# ---------- сборка квиза ----------

@check
def quiz_shape_and_mix():
    rounds = build(fake_sentences(30), seed=1)
    assert len(rounds) == config.ROUNDS and all(len(t) == len(P) for t in rounds)
    kinds = [t[0]["kind"] for t in rounds]
    for k, n in config.MIX.items():
        assert kinds.count(k) == n, (k, kinds)
    for t in rounds:   # в раунде у всех один вид задания
        assert len({q["kind"] for q in t}) == 1


@check
def same_level_within_round_and_growing():
    s = fake_sentences(30)
    level = {generator.sentence_key(x["thai"]): x["level"] for x in s}
    rounds = build(s, seed=2)
    levels = [[level[q["qkey"]] for q in t] for t in rounds if t[0]["kind"] != "letter"]
    assert all(len(set(l)) == 1 for l in levels), levels
    firsts = [l[0] for l in levels]
    assert firsts == sorted(firsts), firsts


@check
def transcription_comes_from_lessons():
    rounds = build(fake_sentences(30), seed=13)
    q = next(q for t in rounds for q in t if q["kind"] == "th_ru")
    tr = q["answer"].split("\n")[1]
    assert tr.startswith("[") and tr.replace("[", "").replace("]", "").replace(" ", "") == q["text"], (q["text"], tr)


@check
def no_repeats_inside_quiz():
    rounds = build(fake_sentences(30), seed=3)
    keys_ = [q["qkey"] for t in rounds for q in t]
    assert len(keys_) == len(set(keys_))


@check
def unknown_words_never_asked():
    rounds = build(fake_sentences(30, bad=10), seed=4)
    assert not any("แม่" in q["answer"] or "แม่" in q["text"] for t in rounds for q in t)


@check
def without_propisi_only_sentences():
    rounds = build(fake_sentences(30), with_letters=False, seed=5)
    assert all(t[0]["kind"] != "letter" for t in rounds) and len(rounds) == config.ROUNDS


@check
def letter_question_points_to_propisi_page():
    rounds = build(fake_sentences(30), seed=6)
    q = next(q for t in rounds for q in t if q["kind"] == "letter")
    letter = q["qkey"].split(":")[1]
    page = generator.propisi.PAGES[letter][1]
    assert str(page) in q["prompt"] and q["extra"]["pdf_page"] == page - 1


@check
def not_enough_sentences_raises():
    try:
        build(fake_sentences(3))
    except generator.NotEnough:
        return
    raise AssertionError("ожидали NotEnough")


@check
def make_quiz_asks_again_when_short():
    calls = []

    def source(lessons, avoid, n, notes=None):
        calls.append(len(avoid))
        return fake_sentences(8, seed=len(calls))

    rounds = generator.make_quiz(LESSONS, {}, P, True, source=source)
    assert len(rounds) == config.ROUNDS and len(calls) >= 2
    assert calls[1] > 0   # во второй раз модели показали уже полученное


@check
def teacher_letters_join_propisi_questions():
    notes = [{"lesson": 6, "letters": ["ศ", "ษ", "zz"], "focus": ""}]   # ศ ษ в уроках ещё не было
    seen = set()
    for seed in range(6):
        rounds = generator.build(LESSONS, {}, fake_sentences(30), P, True, rng=random.Random(seed), notes=notes)
        seen |= {q["qkey"] for t in rounds for q in t if q["kind"] == "letter"}
    assert {"letter:ศ", "letter:ษ"} & seen
    q = generator._letter_q("ศ", {"tr": "", "ru": ""})
    assert q["answer"] == "ศ (ศ.ศาลา) - павильон"   # буквы нет в уроках: перевод из словаря названий
    assert "что значит" in q["prompt"]


@check
def letter_answer_from_lesson_has_reading_and_meaning():
    q = generator._letter_q("ฟ", {"tr": "[фɔ:] [фан]", "ru": "зуб"})
    assert q["answer"] == "ฟ (ฟ.ฟัน) [фɔ:] [фан] - зуб"


@check
def letter_name_words_go_into_sentences():
    les = [{"num": 4, "words": [{"thai": "มา", "tr": "[ма:]", "ru": "приходить"}], "grammar": [], "phrases": [],
            "letters": [{"letter": "ม", "tr": "[мɔ:] [ма:ʹ]", "ru": "лошадь"},
                        {"letter": "ฝ", "tr": "[фɔ:ˇ] «крышка» [фа:ˇ]", "ru": "крышка"},
                        {"letter": "ร", "tr": "[рɔ:]", "ru": "лодка"}]}]   # без второй транскрипции - не берём
    vocab = material.vocabulary(les)
    assert vocab["ม้า"] == {"tr": "[ма:ʹ]", "ru": "лошадь", "lesson": 4}
    assert vocab["ฝา"]["tr"] == "[фа:ˇ]" and "เรือ" not in vocab
    assert material.check_sentence(["ม้า", "มา"], "ม้ามา", vocab) is None
    assert "ม้า" in material.material_for_prompt(les)


@check
def teacher_focus_reaches_sentence_request():
    got = []

    def source(lessons, avoid, n, notes=None):
        got.append(notes)
        return fake_sentences(30)

    notes = [{"lesson": 5, "letters": [], "focus": "отрицание ไม่"}]
    generator.make_quiz(LESSONS, {}, P, True, source=source, notes=notes)
    assert got and got[0] == notes


@check
def slash_transcription_becomes_brackets():
    assert material.norm_tr("/на:й/") == "[на:й]"
    assert material.norm_tr("[йa:й]") == "[йa:й]"
    assert material.norm_tr("тхам") == "[тхам]"
    les = [{"num": 2, "words": [{"thai": "นาย", "tr": "/на:й/", "ru": "начальник"}]}]
    assert material.vocabulary(les)["นาย"]["tr"] == "[на:й]"


# ---------- любое число игроков ----------

@check
def one_player_quiz():
    pl = players(1)
    con = fresh_db()
    rounds = build(fake_sentences(12), keys(pl), seed=20)
    assert all(len(t) == 1 for t in rounds)
    start(con, -100, pl, rounds, now=1.0)
    last = play_all(con, speeds=(2,))
    assert last.final == [(pl[0]["name"], 2.0 * config.ROUNDS)]


@check
def many_players_quiz():
    pl = players(6)
    n = generator.sentences_per_level(len(pl))
    rounds = build(fake_sentences(n, seed=21), keys(pl), seed=21)
    assert all(len(t) == 6 for t in rounds)
    sent = [q["qkey"] for t in rounds for q in t if q["kind"] != "letter"]
    assert len(sent) == len(set(sent))
    con = fresh_db()
    start(con, -100, pl, rounds, now=1.0)
    last = play_all(con, speeds=(6, 5, 4, 3, 2, 1))
    assert [p for p, _ in last.final] == [p["name"] for p in reversed(pl)]
    assert last.wins[pl[-1]["name"]] == config.ROUNDS


@check
def more_players_than_letters():
    pl = players(10)   # 10 игроков * 4 раунда букв > 14 выученных букв
    rounds = build(fake_sentences(generator.sentences_per_level(10), seed=22), keys(pl), seed=22)
    for pos in range(10):   # у каждого буквы в квизе без повторов
        mine = [t[pos]["qkey"] for t in rounds if t[0]["kind"] == "letter"]
        assert len(mine) == len(set(mine)) == config.MIX["letter"]


@check
def request_size_grows_with_players():
    assert generator.sentences_per_level(1) < generator.sentences_per_level(3) < generator.sentences_per_level(8)


# ---------- ход игры ----------

def play_all(con, t0=1000.0, speeds=(3, 2, 1)):
    """Отыгрывает квиз целиком: игрок pos отвечает за speeds[pos] секунд."""
    t = t0
    last = None
    q = game.current(con)
    while q:
        game.mark_shown(con, q, msg_id=q.id, now=t)
        t += speeds[q.pos]
        last = game.press(con, q.id, q.player_id, now=t)
        assert last.status == "ok"
        q = last.next
    return last


@check
def turn_order_and_timing():
    con = fresh_db()
    start(con, -100, PL, build(fake_sentences(30), seed=7), now=1.0)
    q = game.current(con)
    assert (q.round, q.player_id) == (1, PL[0]["id"])
    assert game.press(con, q.id, PL[0]["id"], now=5).status == "stale"   # ещё не показано
    game.mark_shown(con, q, 11, now=10.0)
    assert game.press(con, q.id, PL[1]["id"], now=11).status == "not_yours"
    assert game.press(con, q.id, 999, now=11).status == "not_yours"     # зритель, не игрок
    r = game.press(con, q.id, PL[0]["id"], now=14.5)
    assert r.status == "ok" and abs(r.question.elapsed - 4.5) < 1e-9
    assert r.next.player_id == PL[1]["id"] and r.round_result is None
    assert game.press(con, q.id, PL[0]["id"], now=15).status == "stale"   # повторное нажатие


@check
def round_winner_and_final():
    con = fresh_db()
    start(con, -100, PL, build(fake_sentences(30), seed=8), now=1.0)
    last = play_all(con, speeds=(3, 2, 1))
    assert last.final[0] == (NAMES[2], 20.0) and last.final[-1][0] == NAMES[0]
    assert last.wins[NAMES[2]] == config.ROUNDS
    assert game.current(con) is None and game.active_quiz(con) is None


@check
def round_result_after_last_player():
    con = fresh_db()
    start(con, -100, PL, build(fake_sentences(30), seed=9), now=1.0)
    t = 10.0
    for dt in (5, 2, 7):
        q = game.current(con)
        game.mark_shown(con, q, q.id, now=t)
        t += dt
        r = game.press(con, q.id, q.player_id, now=t)
    assert r.round_result[0] == (NAMES[1], 2.0) and len(r.round_result) == 3


@check
def restart_starts_over_and_does_not_repeat():
    con = fresh_db()
    sentences = fake_sentences(60, seed=11)
    r1 = build(sentences, used=store.used_keys(con), seed=10)
    start(con, -100, PL, r1, now=1.0)
    for _ in range(9):   # отыграли три раунда и перезапустили
        q = game.current(con)
        game.mark_shown(con, q, q.id, now=2.0)
        game.press(con, q.id, q.player_id, now=3.0)
    shown = {(q["qkey"], P[pos]) for t in r1[:3] for pos, q in enumerate(t)}
    r2 = build(sentences, used=store.used_keys(con), seed=11)
    start(con, -100, PL, r2, now=10.0)
    q = game.current(con)
    assert (q.round, q.pos) == (1, 0)
    for t in r2:
        for pos, x in enumerate(t):
            assert (x["qkey"], P[pos]) not in shown, x
    # неразыгранные вопросы первого квиза не считаются заданными
    assert r1[5][0]["qkey"] not in store.used_keys(con)[P[0]]


@check
def history_follows_person_not_seat():
    """Игрок сел вторым вместо первого - его история всё равно учитывается."""
    con = fresh_db()
    sentences = fake_sentences(60, seed=14)
    r1 = build(sentences, used=store.used_keys(con), seed=14)
    for t in r1:
        for pos, q in enumerate(t):
            store.mark_used(con, P[pos], q["qkey"], t=5.0)
    swapped = [P[1], P[0], P[2]]
    r2 = build(sentences, swapped, used=store.used_keys(con), seed=15)
    seen_by = {P[pos]: {q["qkey"] for t in r1 for q in [t[pos]] if q["kind"] != "letter"} for pos in range(3)}
    for t in r2:
        for pos, q in enumerate(t):
            assert q["qkey"] not in seen_by[swapped[pos]]


@check
def letters_cycle_through_before_repeating():
    con = fresh_db()
    seen = {p: [] for p in P}
    for i in range(3):
        rounds = build(fake_sentences(30, seed=i), used=store.used_keys(con), seed=i)
        for t in rounds:
            for pos, q in enumerate(t):
                if q["kind"] == "letter":
                    seen[P[pos]].append(q["qkey"])
                store.mark_used(con, P[pos], q["qkey"], t=100.0 + i)
    for p in P:   # 12 букв за три квиза из 14 выученных - без повторов
        assert len(seen[p]) == 12 and len(set(seen[p])) == 12, seen[p]


@check
def quiz_waits_for_begin_button():
    con = fresh_db()
    qid = game.start(con, -100, PL, build(fake_sentences(30), seed=30), now=1.0)
    assert game.current(con) is None and game.active_quiz(con) is None   # собран, но заданий нет
    assert game.begin(con, qid, now=50.0)
    assert game.current(con).round == 1
    assert not game.begin(con, qid)   # второе нажатие «Начинаем» ничего не делает


@check
def stop_and_restart_cancel_ready_quiz():
    con = fresh_db()
    q1 = game.start(con, -100, PL, build(fake_sentences(30), seed=31), now=1.0)
    assert game.stop(con)
    assert not game.begin(con, q1)   # старая кнопка после «Стоп»
    q2 = game.start(con, -100, PL, build(fake_sentences(30), seed=32), now=2.0)
    q3 = game.start(con, -100, PL, build(fake_sentences(30), seed=33), now=3.0)
    assert not game.begin(con, q2) and game.begin(con, q3)   # кнопка заменённого квиза не работает


@check
def session_collects_messages_until_end():
    con = fresh_db()
    chat = -100
    assert store.current_session(con, chat) is None
    store.track(con, chat, 5)                       # до «Начали» ничего не копится
    store.open_session(con, chat, 10)
    for m in (10, 11, 12, 12):
        store.track(con, chat, m)
    assert store.session_msgs(con, 10) == [10, 11, 12]
    store.open_session(con, chat, 50)               # новое «Начали», старый квиз не убран
    store.track(con, chat, 51)
    assert store.session_msgs(con, 10) == [10, 11, 12] and store.session_msgs(con, 50) == [51]
    store.close_session(con, chat, 10)              # «Конец» старого не трогает новый
    assert store.session_msgs(con, 10) == [] and store.current_session(con, chat) == 50
    store.close_session(con, chat, 50)
    assert store.current_session(con, chat) is None
    store.track(con, chat, 60)
    assert store.session_msgs(con, 50) == []


# ---------- ведущий ----------

@check
def host_script_survives_bad_model_output():
    s = host.Script("привет", [["a", "b"]], ["{winner} за {time}", "сломано {oops}", "{"], ["{winner}!", "{x}"])
    assert s.round_comments == ["{winner} за {time}"] and s.finale == ["{winner}!"]
    assert s.intro(1, 0, "Анна") == "a"
    assert "Вера" in s.intro(1, 2, "Вера")   # подводки не хватило - запасная с именем
    assert "Борис" in s.intro(7, 1, "Борис")   # раунда нет вовсе
    assert s.round_comment("Борис", "3.0 с") == "Борис за 3.0 с"


@check
def host_drops_gendered_and_case_broken_templates():
    """Шаблоны из настоящего прогона GPT 26.09: имя подставляется как есть, в именительном падеже."""
    bad = ["Самая быстрая реакция у {winner}: {time}!", "Быстрее всех оказался {winner}: {time}.",
           "Вот это темп: {winner} справился за {time}!", "На финише первым оказался {winner}, время - {time}.",
           "{winner} сделал рывок и остановил таймер на {time}!"]
    good = ["{winner}, молниеносно! Раунд ваш!", "Секундомер аплодирует: {winner}, {time}!",
            "Быстрее всех в раунде: {winner}! Время {time}.", "🔥 {winner} за {time}! Вот это скорость!"]
    s = host.Script("", [], bad + good, ["Сегодняшний чемпион: {winner}!", "Победу празднует {winner}!"])
    assert s.round_comments == good, s.round_comments
    assert s.finale == ["Сегодняшний чемпион: {winner}!"]
    for t in host.FALLBACK_ROUND:
        assert host._ok_template(t, "winner", "time"), t


@check
def host_script_roundtrip_and_fallback():
    s = host.fallback_script(NAMES, rng=random.Random(1))
    assert len(s.intros) == config.ROUNDS and all(n in s.intros[0][i] for i, n in enumerate(NAMES))
    s2 = host.Script.from_json(s.to_json())
    assert s2.intros == s.intros and s2.opening == s.opening
    assert host.Script.from_json(None).intro(1, 0, "Кто-то")


@check
def host_failure_gives_fallback():
    class Broken:
        class responses:
            @staticmethod
            def create(**kw):
                raise RuntimeError("нет сети")
    s = host.quiz_script(["Аня", "Боря"], client=Broken())
    assert len(s.intros) == config.ROUNDS and len(s.intros[0]) == 2
    assert "6" in host.lesson_announcement(6, {}, client=Broken())


@check
def quiz_keeps_host_script_and_players():
    con = fresh_db()
    s = host.fallback_script(NAMES)
    qid = start(con, -100, PL, build(fake_sentences(30), seed=12), now=1.0, script=s.to_json())
    assert host.Script.from_json(game.script_json(con, qid)).intros == s.intros
    assert game.quiz_players(con, qid) == PL


if __name__ == "__main__":
    import logging
    logging.disable(logging.CRITICAL)   # запасной режим ведущего пишет трейсбэки, в тестах они ожидаемы
    failed = 0
    for fn in checks:
        try:
            fn()
            print("ok  ", fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
    print(f"\n{len(checks) - failed}/{len(checks)} проверок прошло")
    sys.exit(1 if failed else 0)
