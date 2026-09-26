import json
import time
from dataclasses import dataclass, field

import store


@dataclass
class Question:
    id: int
    quiz_id: int
    round: int
    pos: int
    player_id: int
    player: str
    kind: str
    prompt: str
    answer: str
    qkey: str
    extra: dict
    msg_id: int | None
    shown_at: float | None
    done_at: float | None

    @property
    def elapsed(self) -> float | None:
        if self.shown_at is None or self.done_at is None:
            return None
        return self.done_at - self.shown_at


@dataclass
class PressResult:
    status: str
    question: "Question | None" = None
    next: "Question | None" = None
    round_result: list[tuple[str, float]] | None = None
    final: list[tuple[str, float]] | None = None
    total_rounds: int = 0
    wins: dict[str, int] = field(default_factory=dict)


def _q(row) -> Question:
    d = dict(row)
    d["extra"] = json.loads(d["extra"] or "{}")
    return Question(**d)


def active_quiz(con):
    return con.execute("SELECT * FROM quiz WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()


def start(con, chat_id: int, players: list[dict], rounds: list[list[dict]], now: float | None = None,
          script: str | None = None) -> int:
    now = now or time.time()
    with con:
        con.execute("UPDATE quiz SET status='stopped', finished_at=? WHERE status IN ('ready', 'active')", (now,))
        qid = con.execute(
            "INSERT INTO quiz (chat_id, status, started_at, players, script) VALUES (?, 'ready', ?, ?, ?)",
            (chat_id, now, json.dumps(players, ensure_ascii=False), script)).lastrowid
        for r, row in enumerate(rounds, 1):
            for pos, q in enumerate(row):
                p = players[pos]
                con.execute(
                    "INSERT INTO questions (quiz_id, round, pos, player_id, player, kind, prompt, answer, qkey, extra)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (qid, r, pos, p["id"], p["name"], q["kind"], q["prompt"], q["answer"], q["qkey"],
                     json.dumps({**q.get("extra", {}), "text": q.get("text", "")}, ensure_ascii=False)))
    return qid


def begin(con, quiz_id: int, now: float | None = None) -> bool:
    with con:
        cur = con.execute("UPDATE quiz SET status='active', started_at=? WHERE id=? AND status='ready'",
                          (now or time.time(), quiz_id))
    return cur.rowcount > 0


def stop(con, now: float | None = None) -> bool:
    with con:
        cur = con.execute("UPDATE quiz SET status='stopped', finished_at=? WHERE status IN ('ready', 'active')",
                          (now or time.time(),))
    return cur.rowcount > 0


def current(con) -> Question | None:
    quiz = active_quiz(con)
    if not quiz:
        return None
    row = con.execute("SELECT * FROM questions WHERE quiz_id=? AND done_at IS NULL ORDER BY round, pos LIMIT 1",
                      (quiz["id"],)).fetchone()
    return _q(row) if row else None


def quiz_players(con, quiz_id: int) -> list[dict]:
    row = con.execute("SELECT players FROM quiz WHERE id=?", (quiz_id,)).fetchone()
    return json.loads(row["players"]) if row and row["players"] else []


def script_json(con, quiz_id: int) -> str | None:
    row = con.execute("SELECT script FROM quiz WHERE id=?", (quiz_id,)).fetchone()
    return row["script"] if row else None


def total_rounds(con, quiz_id: int) -> int:
    return con.execute("SELECT MAX(round) FROM questions WHERE quiz_id=?", (quiz_id,)).fetchone()[0] or 0


def mark_shown(con, q: Question, msg_id: int, now: float | None = None):
    now = now or time.time()
    with con:
        con.execute("UPDATE questions SET msg_id=?, shown_at=? WHERE id=?", (msg_id, now, q.id))
    store.mark_used(con, q.player_id, q.qkey, now)
    q.msg_id, q.shown_at = msg_id, now


def round_result(con, quiz_id: int, rnd: int) -> list[tuple[str, float]]:
    rows = con.execute("SELECT * FROM questions WHERE quiz_id=? AND round=? ORDER BY pos", (quiz_id, rnd)).fetchall()
    return sorted(((q.player, q.elapsed) for q in map(_q, rows)), key=lambda x: x[1])


def standings(con, quiz_id: int) -> tuple[list[tuple[str, float]], dict[str, int]]:
    names = [p["name"] for p in quiz_players(con, quiz_id)]
    rows = [_q(r) for r in con.execute("SELECT * FROM questions WHERE quiz_id=? AND done_at IS NOT NULL", (quiz_id,))]
    totals = {n: 0.0 for n in names}
    by_round: dict[int, list[Question]] = {}
    for q in rows:
        totals[q.player] = totals.get(q.player, 0.0) + q.elapsed
        by_round.setdefault(q.round, []).append(q)
    wins = {n: 0 for n in names}
    for qs in by_round.values():
        if len(qs) == len(names):
            w = min(qs, key=lambda x: x.elapsed).player
            wins[w] = wins.get(w, 0) + 1
    return sorted(totals.items(), key=lambda x: x[1]), wins


def press(con, question_id: int, user_id: int, now: float | None = None) -> PressResult:
    now = now or time.time()
    q = current(con)
    if q is None or q.id != question_id or q.shown_at is None:
        return PressResult("stale")
    if q.player_id != user_id:
        return PressResult("not_yours", question=q)
    with con:
        con.execute("UPDATE questions SET done_at=? WHERE id=?", (now, q.id))
    q.done_at = now
    res = PressResult("ok", question=q, total_rounds=total_rounds(con, q.quiz_id))
    res.next = current(con)
    if res.next is None or res.next.round != q.round:
        res.round_result = round_result(con, q.quiz_id, q.round)
    if res.next is None:
        with con:
            con.execute("UPDATE quiz SET status='done', finished_at=? WHERE id=?", (now, q.quiz_id))
        res.final, res.wins = standings(con, q.quiz_id)
    return res
