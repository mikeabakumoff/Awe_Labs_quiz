# Awe_Labs_quiz

A Telegram quiz host for a small group of friends learning Thai together. The
group owner forwards lesson PDFs into the chat; the bot reads them, keeps a
running course dictionary, and on command runs a 20-round speed quiz built from
everything covered so far: say this in Thai, translate this from Thai, and read
this letter from the handwriting workbook page it sends.

The interesting part is not that an LLM writes the questions. It is what the
code refuses to take on trust: every sentence the model produces is checked
against the lessons' own vocabulary, the answer transcription is assembled from
the textbook rather than from the model, and the host's jokes are generated in
one batch and filtered before a single one reaches the chat.

## What it does

- **Lesson intake**: a forwarded PDF is saved, removed from the chat, and parsed
  into letters, vowels, words, grammar rules and example phrases
- **Teacher notes**: a forwarded message from a teacher ("practise workbook pages
  39-41, revise negation") is parsed into letters to drill and a topic to lean on,
  and both feed into the next quiz
- **Lobby**: any member can join with a button; the owner closes the lobby, the
  quiz is prepared, and only then does the owner start it
- **Rounds**: every player gets a different task of the same kind and the same
  difficulty level; difficulty climbs towards the end
- **Scoring**: time from the task appearing to the player pressing *Done*;
  fastest in a round wins it, lowest total time wins the quiz
- **No repeats**: a question shown to a player is never shown to that player
  again, across quizzes and restarts
- **Host**: a cheerful presenter introduces every task, comments on each round
  and crowns the winner
- **Clean-up**: after the results, one button deletes the whole quiz from the chat

## Architecture notes

### Vocabulary is enforced, not requested

The prompt asks for sentences built only from lesson words, and models mostly
comply - mostly. So each generated sentence comes back as a list of words, and
`material.check_sentence` rejects it unless every word is in the dictionary and
the words concatenate to the sentence. Rejected sentences are simply dropped;
if too few survive, the generator asks again with everything already produced
added to the avoid list.

The dictionary includes the name words of learned letters (ม - ม้า "horse",
ฟ - ฟัน "tooth"), so letters get revised inside sentences too.

### The model does not write the answer key's transcription

In early dry runs the model's transcriptions drifted from the textbook's system
(tone marks dropped, long vowels shortened). The answer now shows a
transcription joined together from the per-word transcriptions extracted from
the lessons, so it always matches what the students were taught.

### Two models, split by job

- **Claude** (`claude-sonnet-5`) does the structured work: lesson extraction,
  question generation, parsing teacher notes. All three use JSON-schema
  structured outputs, so the code never parses free text.
- **GPT** (`gpt-5.6-luna`) does everything creative: host lines and lesson
  announcements.

Host lines for a whole quiz are generated in one request while the questions
are being built, so nothing waits on a model during play. The round comments
are templates with `{winner}` and `{time}` placeholders, and the code throws out
any template that does not format, and any that would put a name in a slot
where Russian grammar needs a case ending or reveals gender ("у {winner}",
"{winner} справился") - the model does this regularly despite the prompt. If
the request fails outright, a built-in phrase set takes over.

### Game state survives restarts

SQLite holds quizzes, questions and timestamps (wall clock, not monotonic), so a
bot restart mid-round loses nothing: the *Done* button on the last task still
works and the timer is still right. A quiz moves through `ready → active →
done/stopped`; a stale button from a replaced or stopped quiz does nothing. Only
the current player's press on the current task counts; a second press is ignored.

A question is marked as used when it is *shown*, not when it is generated, so a
quiz stopped halfway does not burn the questions nobody saw.

### Lesson PDFs

The lesson PDFs have a text layer that mangles Thai combining marks (ำ splits,
ี and ั vanish), so extraction relies on the model reading the page images;
the prompt says so explicitly.

### One group, one owner

A handler registered before all others drops every update that is not from the
configured group, and the bot leaves any other group it is added to. Starting,
stopping, lesson intake and clean-up are owner-only; everyone else can join the
lobby and press *Done* on their own task.

Clean-up works by tracking every message in the chat from the moment a quiz is
announced. Telegram lets bots delete messages only within 48 hours, which is the
practical deadline for pressing the button.

## What is not in this repository

The lesson PDFs, the handwriting workbook, the audio and the database. They are
course materials and the group's own history. The code runs without them: with
no lessons it tells the owner to send one, and without the workbook the letter
rounds are replaced with sentence rounds.

This is a sanitized copy of a bot in use: player names in tests are made up, and
the owner and group are configured through the environment.

## Layout

| File | |
|---|---|
| `bot.py` | Telegram layer: group gate, lobby, buttons, lesson and note intake, clean-up |
| `game.py` | quiz flow without Telegram: states, timing, round and final standings |
| `generator.py` | builds a quiz: sentences from the model, letter rounds, no repeats per player |
| `material.py` | lesson and note extraction, dictionary, sentence check |
| `host.py` | presenter lines, template filtering, fallback phrases |
| `propisi.py` | workbook page for each consonant, letter-name meanings, page rendering |
| `store.py` | SQLite schema and access |
| `config.py` | rounds, round mix, models, owner and group |
| `ingest.py` | parse a lesson from the command line |
| `dry_run.py` | build a full quiz and print it, without Telegram |
| `check_state.py` | is a quiz running (check before a restart) |
| `tests/test_exam.py` | game and generator logic, no network, no models |
| `deploy/thai-exam.service` | systemd unit |

## Running

```bash
python -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # token, API keys, owner and group ids
sudo apt install poppler-utils   # pdftoppm renders workbook pages
venv/bin/python bot.py
```

Tests: `python tests/test_exam.py`.

In the bot's settings (BotFather), privacy mode must be off so it can read plain
messages in the group, and the bot needs admin rights to delete messages.
