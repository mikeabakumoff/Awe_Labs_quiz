import concurrent.futures as cf
import sys

import config
import generator
import host
import propisi
import store


def main(names: list[str]):
    con = store.connect()
    lessons = store.lessons(con)
    keys = [f"dry-{i}" for i in range(len(names))]
    with cf.ThreadPoolExecutor() as ex:
        f_rounds = ex.submit(generator.make_quiz, lessons, store.used_keys(con), keys, propisi.available())
        f_host = ex.submit(host.quiz_script, names, config.ROUNDS)
        rounds, script = f_rounds.result(), f_host.result()
    print("ОТКРЫТИЕ:", script.opening, "\n")
    for r, row in enumerate(rounds, 1):
        for pos, q in enumerate(row):
            print(f"[{r}] {names[pos]} | {script.intro(r, pos, names[pos])}")
            print(f"     {q['prompt']} {q.get('text', '')}")
            print(f"     ответ: {q['answer'].replace(chr(10), ' ')}")
        print("     итог:", script.round_comment(names[-1], "4.2 с"))
    print("\nФИНАЛ:", script.finale_line(names[0]))


if __name__ == "__main__":
    main(sys.argv[1:] or ["Анна", "Борис", "Вера"])
