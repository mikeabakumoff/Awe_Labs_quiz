import json
import random
import re

import config

HOST_STYLE = """Ты весёлый ведущий телевикторины в телеграм-группе, где друзья вместе учат тайский. Тон дружеский, задорный, с азартом и лёгким юмором, как у ведущего игрового шоу. Обращайся на «вы» по имени. Пол игроков по имени не угадывай: без родовых окончаний (не «готов/готова», а «вы готовы?»). Коротко: одна-две фразы. Можно эмодзи, но не больше одного-двух. Не используй длинное и среднее тире, только обычный дефис. Не пиши сами задания и ответы, только подводку."""

QUIZ_PROMPT = HOST_STYLE + """

Квиз из {rounds} раундов, игроков {n}, в каждом раунде по очереди отвечают: {players}. Задания трёх видов: сказать фразу по-тайски, перевести тайскую фразу на русский, прочитать букву на странице прописей. Какое задание в каком раунде, заранее неизвестно, поэтому в подводках вид задания не называй.

Напиши:
- opening: объявление, что квиз подготовлен: заведите всех игроков и закончите вопросом, начинаем ли, в духе «Всё готово! Начинаем?»;
- intros: для каждого раунда по порядку массив из {n} подводок к заданию, по одной для каждого игрока в том же порядке: {players}. Каждая подводка обращается к игроку по имени и подзадоривает, например «Итак, Анна! Спецзадание для вас. Как быстро справитесь?!». Все подводки разные, без повторов, иногда с отсылкой к номеру раунда, к счёту или к тому, что финал близко;
- round_comments: 25 разных комментариев к итогу раунда с подстановками {{winner}} (имя самого быстрого) и {{time}} (его время), например «{{winner}} за {{time}}! Вот это скорость!»;
- finale: 3 варианта торжественного поздравления победителя квиза с подстановкой {{winner}}.

Подстановка {{winner}} заменяется именем в именительном падеже (любое из имён игроков), оно бывает мужским и женским. Ставь {{winner}} только туда, где подходит именительный падеж и не виден род: обращение в начале фразы, подлежащее при глаголе настоящего времени или после двоеточия. Плохо: «победа достаётся {{winner}}», «объявляем победителем {{winner}}», «{{winner}} справился». Хорошо: «{{winner}}, это было молниеносно!», «Быстрее всех в раунде: {{winner}}!»."""

LESSON_PROMPT = HOST_STYLE + """

В квиз только что добавлен новый урок. Напиши радостное объявление на два-три предложения в духе «Теперь в квизе ещё больше вопросов: добавлен урок {num}!», дальше что в уроке нового (коротко, по материалу ниже) и что это пригодится на квизе. Пиши безлично, как новость: не упоминай, кто добавил урок, никаких имён («Анна добавила» и т.п. нельзя). Верни только текст объявления.

Материал урока: {summary}"""

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "opening": {"type": "string"},
        "intros": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "round_comments": {"type": "array", "items": {"type": "string"}},
        "finale": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["opening", "intros", "round_comments", "finale"],
    "additionalProperties": False,
}


FALLBACK_OPENING = [
    "🎤 Дамы и господа, вопросы разложены по конвертам, секундомер заряжен! Всё готово. Начинаем?",
    "🎤 Внимание, студия! Квиз по тайскому собран, игроки на местах. Ну что, начинаем?",
]
FALLBACK_INTRO = [
    "Итак, {name}! Спецзадание для вас. Как быстро справитесь?!",
    "{name}, ваш выход! Часы уже тикают ⏱",
    "Внимание, {name}! Покажите, чему научились!",
    "{name}, зрители замерли в ожидании!",
    "А теперь {name}! Соперники следят за каждой секундой.",
    "{name}, вот задачка как раз для вас. Поехали!",
    "Микрофон переходит к игроку по имени {name}! Жмите «Готово», как только ответите.",
    "{name}, ставки растут! Не подведите!",
]
FALLBACK_ROUND = [
    "{winner} за {time}! Вот это скорость! 🔥",
    "Раунд забирает: {winner}, всего {time}. Остальные, догоняйте!",
    "{winner}: {time}. Браво!",
    "Молниеносно! {winner}, {time}.",
    "Победа в раунде: {winner}, {time}!",
]
FALLBACK_FINALE = ["🏆 Встречайте чемпиона: {winner}! Аплодисменты!",
                   "🏆 {winner} берёт главный приз сегодняшнего квиза! Спасибо всем за игру!"]
FALLBACK_LESSON = ["🎉 Теперь в квизе ещё больше вопросов: добавлен урок {num}! Новые слова уже ждут вас.",
                   "📚 В квиз добавлен урок {num}! Запоминаем, повторяем, скоро проверим!"]


def _ok_template(t: str, *keys: str) -> bool:
    try:
        t.format(**{k: "x" for k in keys})
    except (KeyError, IndexError, ValueError):
        return False
    return "winner" not in keys or _neutral_winner(t)


_GENDERED_AFTER = re.compile(r"\{winner\}\s+[а-яё]+(л|ла|лся|лась)\b", re.I)


def _neutral_winner(t: str) -> bool:
    if not (re.match(r"^\W*\{winner\}", t) or re.search(r"[:!]\s*\{winner\}", t)):
        return False
    return not _GENDERED_AFTER.search(t)


class Script:

    def __init__(self, opening: str, intros: list[list[str]], round_comments: list[str], finale: list[str],
                 rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.opening = opening
        self.intros = intros
        self.round_comments = [t for t in round_comments if _ok_template(t, "winner", "time")] or FALLBACK_ROUND
        self.finale = [t for t in finale if _ok_template(t, "winner")] or FALLBACK_FINALE

    def intro(self, rnd: int, pos: int, name: str) -> str:
        try:
            line = self.intros[rnd - 1][pos]
            if line.strip():
                return line
        except IndexError:
            pass
        return self.rng.choice(FALLBACK_INTRO).format(name=name)

    def round_comment(self, winner: str, time: str) -> str:
        return self.rng.choice(self.round_comments).format(winner=winner, time=time)

    def finale_line(self, winner: str) -> str:
        return self.rng.choice(self.finale).format(winner=winner)

    def to_json(self) -> str:
        return json.dumps({"opening": self.opening, "intros": self.intros,
                           "round_comments": self.round_comments, "finale": self.finale}, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str | None) -> "Script":
        if not s:
            return fallback_script([])
        d = json.loads(s)
        return cls(d["opening"], d["intros"], d["round_comments"], d["finale"])


def fallback_script(players: list[str], rounds: int = config.ROUNDS, rng: random.Random | None = None) -> Script:
    rng = rng or random.Random()
    intros = [[rng.choice(FALLBACK_INTRO).format(name=p) for p in players] for _ in range(rounds)]
    return Script(rng.choice(FALLBACK_OPENING), intros, FALLBACK_ROUND, FALLBACK_FINALE, rng)


def _gpt(prompt: str, schema: dict | None = None, client=None) -> str:
    from openai import OpenAI
    client = client or OpenAI()
    kw = {}
    if schema:
        kw["text"] = {"format": {"type": "json_schema", "name": "host", "schema": schema, "strict": True}}
    r = client.responses.create(model=config.HOST_MODEL, input=prompt, **kw)
    return r.output_text.replace("—", "-").replace("–", "-")


def quiz_script(players: list[str], rounds: int = config.ROUNDS, client=None) -> Script:
    try:
        prompt = QUIZ_PROMPT.format(rounds=rounds, n=len(players), players=", ".join(players))
        d = json.loads(_gpt(prompt, QUIZ_SCHEMA, client))
        return Script(d["opening"], d["intros"], d["round_comments"], d["finale"])
    except Exception:
        import logging
        logging.getLogger(__name__).exception("host script failed, using fallback")
        return fallback_script(players, rounds)


def lesson_announcement(num: int, data: dict, client=None) -> str:
    try:
        summary = ("новые буквы: " + " ".join(l["letter"] for l in data.get("letters", []))
                   + "; слова: " + ", ".join(f"{w['thai']} ({w['ru']})" for w in data.get("words", [])[:15])
                   + "; правила: " + "; ".join(g["title"] for g in data.get("grammar", [])))
        text = _gpt(LESSON_PROMPT.format(num=num, summary=summary), client=client).strip()
        if text:
            return text
    except Exception:
        import logging
        logging.getLogger(__name__).exception("lesson announcement failed")
    return random.choice(FALLBACK_LESSON).format(num=num)
