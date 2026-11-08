"""Бенчмарк локальных LLM под роли агента. Запуск: uv run python task3-agent/evals/llm_bench.py [--think] [--models ...]"""
import argparse
import json
import random
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

import ollama

HERE = Path(__file__).parent
TASKS = HERE / "bench_tasks.jsonl"
OUT = HERE / "results"
MODELS = ["qwen3:8b", "qwen3.5:4b", "qwen3.5:9b", "gemma4:e4b", "gemma4:12b", "gpt-oss:20b"]
OPTIONS = {"temperature": 0, "num_ctx": 32768, "num_predict": 2048}  # без лимита модель может зациклиться
THINK_PREDICT = 8192  # рассуждение тоже тратит num_predict
ANSWER_CHARS = 2000  # сколько ответа хранить в результатах для разбора и rescore
# trust_env=False: иначе httpx шлёт запросы к localhost через HTTP_PROXY, и прокси рвёт долгие ответы
client = ollama.Client(trust_env=False)


def fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}


S = {"type": "string"}
TOOLS = [
    fn("transcribe", "Расшифровать аудиофайл из workspace/", {"path": S, "language": S}, ["path"]),
    fn("sentiment", "Тональность английского текста: POSITIVE или NEGATIVE", {"text": S}, ["text"]),
    fn("classify_image", "Топ-k классов ImageNet для картинки из workspace/", {"path": S, "k": {"type": "integer"}}, ["path"]),
    fn("detect_video", "Найти и посчитать объекты на видео из workspace/", {"path": S, "conf": {"type": "number"}}, ["path"]),
    fn("run_python", "Выполнить Python-код в песочнице, файлы лежат в workspace/", {"code": S}, ["code"]),
    fn("memory_search", "Найти факты в долгой памяти", {"query": S}, ["query"]),
    fn("load_skill", "Загрузить инструкцию-скилл",
       {"name": {"type": "string", "enum": ["video-analysis", "speech-report", "data-analysis", "answer-with-evidence"]}}, ["name"]),
]
SYSTEM = {
    "tools": "Ты — медиа-ассистент. Файлы пользователя лежат в workspace/. Вызывай тул, если он нужен для ответа. Если тул не нужен — отвечай сам.",
    "hallucination": "Отвечай кратко. Если не знаешь ответа или в контексте нет данных — прямо скажи, что не знаешь.",
}
IDK = re.compile(
    r"не знаю|не могу (найти|ответить|определить|подтвердить|назвать)|не наш[её]л|нет (\w+ )?(данных|информации|сведений)|нет (такого|такой|статьи)|"
    r"не (указан|упомина|существ|располага|содерж|говорится|был|выпуска|публикова|является|являлся|получал)|неизвестн|вымышлен|не удалось|отсутству|"
    r"(невозможно|нельзя) (определить|сказать|ответить)|такой \w+ нет|don't know|no information|not mentioned|does(n't| not) exist|fictional",
    re.I)
# эмодзи в репо — редфлаг, поэтому в результатах они заменяются меткой и считаются отдельно
EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]")


def haystack(chars, needle, pos, seed):
    rnd = random.Random(seed)
    lines, size = [], 0
    while size < chars:
        s = (f"Запись {len(lines) + 1}: датчик {rnd.randint(1, 40)} в цехе {rnd.randint(1, 9)} "
             f"показал {rnd.randint(-5, 35)} °C и влажность {rnd.randint(20, 90)}%.")
        lines.append(s)
        size += len(s) + 1
    lines.insert(int(len(lines) * pos), needle)
    return "\n".join(lines)


def messages(task):
    if "long" in task:
        L = task["long"]
        doc = haystack(L["chars"], L["needle"], L["pos"], L["seed"])
        return [{"role": "user", "content": f"Документ:\n{doc}\n\nВопрос: {L['question']}"}]
    prompt = f"Контекст: {task['context']}\n\nВопрос: {task['prompt']}" if task.get("context") else task["prompt"]
    system = SYSTEM.get(task["cat"])
    return ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]


def match(value, exp):
    if isinstance(exp, list):
        return any(match(value, e) for e in exp)
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        try:
            return abs(float(value) - exp) < 1e-6
        except (TypeError, ValueError):
            return False
    return value is not None and str(exp).lower() in str(value).lower()


def parse_json(text):
    return json.loads(re.sub(r"^```(json)?\s*|\s*```$", "", text.strip()))


def json_ok(data, rules):
    for r in rules:
        v = data
        for k in filter(None, r.get("path", "").split(".")):
            v = v.get(k) if isinstance(v, dict) else None
        if "type" in r and type(v).__name__ != r["type"]:
            return False
        if "in" in r and v not in r["in"]:
            return False
        if "contains" in r and not match(v, r["contains"]):
            return False
        if "eq" in r and not match(v, r["eq"]):
            return False
        if "len" in r and (not isinstance(v, list) or len(v) != r["len"]):
            return False
        if "sum" in r and (not isinstance(v, list)
                           or not match(sum(x.get(r["sum"], 0) for x in v if isinstance(x, dict)), r["sum_eq"])):
            return False
    return True


DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-") | {0xA0: " ", 0x202F: " "}


def check(task, msg):
    e = task["expect"]
    calls = msg.tool_calls or []
    text = (msg.content or "").translate(DASHES)  # gpt-oss пишет «янтарь‑731» с неразрывным дефисом
    if "tool" in e:
        if not calls:
            return False
        c = calls[0].function
        return c.name == e["tool"] and all(match(c.arguments.get(k), v) for k, v in e.get("args", {}).items())
    if calls:
        return False
    if e.get("no_tool"):
        return bool(text.strip())
    if e.get("idk"):
        # not_regex ловит выдуманные детали рядом с отказом
        return bool(IDK.search(text)) and not (e.get("not_regex") and re.search(e["not_regex"], text, re.I))
    if "regex" in e:
        rx = [e["regex"]] if isinstance(e["regex"], str) else e["regex"]
        return all(re.search(r, text, re.I) for r in rx)
    if "json" in e:
        try:
            return json_ok(parse_json(text), e["json"])
        except (ValueError, TypeError, AttributeError):
            return False
    if e.get("cyrillic"):
        letters = re.findall(r"[^\W\d_]", text)
        return bool(letters) and sum(bool(re.match(r"[а-яё]", ch, re.I)) for ch in letters) / len(letters) >= 0.85
    raise ValueError(f"нет проверки для {task['id']}")


def think_arg(model, think):
    # gpt-oss не отключает рассуждение полностью, только уровень
    if model.startswith("gpt-oss"):
        return "high" if think else "low"
    return think


def run(model, task, think):
    t = time.perf_counter()
    row = {"model": model, "think": think, "id": task["id"], "cat": task["cat"]}
    for _ in range(3):
        try:
            r = client.chat(model=model, messages=messages(task), tools=TOOLS if task["cat"] == "tools" else None,
                            options=OPTIONS | ({"num_predict": THINK_PREDICT} if think else {}),
                            think=think_arg(model, think), keep_alive="10m")
            break
        except ConnectionError as ex:  # после обрыва длинного запроса сервер отвечает не сразу
            err = ex
            time.sleep(10)
        except Exception as ex:
            return row | {"ok": False, "error": str(ex)[:200], "latency_s": round(time.perf_counter() - t, 2)}
    else:
        return row | {"ok": False, "error": str(err)[:200], "latency_s": round(time.perf_counter() - t, 2)}
    return row | {
        "ok": check(task, r.message),
        "latency_s": round(time.perf_counter() - t, 2),
        "tok_per_s": round(r.eval_count / r.eval_duration * 1e9, 1) if r.eval_duration else None,
        "prompt_tokens": r.prompt_eval_count,
        "answer": EMOJI.sub("[emoji]", (r.message.content or "")[:ANSWER_CHARS]),
        "truncated": len(r.message.content or "") > ANSWER_CHARS,
        "emoji": len(EMOJI.findall(r.message.content or "")),
        "tool_calls": [{"name": c.function.name, "args": c.function.arguments} for c in (r.message.tool_calls or [])],
    }


def warm_up(model):
    # первый вызов грузит модель: его load_duration — холодный старт
    r = client.chat(model=model, messages=[{"role": "user", "content": "ok"}], options=OPTIONS,
                    think=think_arg(model, False), keep_alive="10m")
    ram = next((m.size for m in client.ps().models if m.model == model), 0)
    return round(r.load_duration / 1e9, 1), round(ram / 1e9, 1)


def rescore(rows):
    # перепроверяет сохранённые ответы текущими проверками; обрезанный ответ сохраняет старый вердикт
    tasks = {t["id"]: t for t in map(json.loads, filter(str.strip, TASKS.read_text().splitlines()))}
    for r in rows:
        ans = r.get("answer") or ""
        if r.get("error") or r.get("truncated", len(ans) >= 300):  # 300 — лимит первых прогонов без флага
            continue
        calls = [NS(function=NS(name=c["name"], arguments=c["args"])) for c in r.get("tool_calls") or []]
        r["ok"] = check(tasks[r["id"]], NS(content=ans, tool_calls=calls))
    return rows


def load_results():
    rows, meta = [], {}
    for f in sorted(OUT.glob("bench-*.jsonl")):
        for line in f.read_text().splitlines():
            r = json.loads(line)
            key = (r["model"], r["think"])
            if r.get("meta"):
                meta[key] = r
                rows = [x for x in rows if (x["model"], x["think"]) != key]  # новый прогон заменяет старый
            else:
                rows.append(r)
    return rows, meta


def summarize(rows, meta):
    cats = list(dict.fromkeys(r["cat"] for r in rows))
    head = ["Модель", "think"] + cats + ["Итого", "tok/s", "p50, с", "RAM, ГБ", "Холодный старт, с"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for (model, think), m in meta.items():
        rs = [r for r in rows if r["model"] == model and r["think"] == think]
        acc = [f"{sum(r['ok'] for r in rs if r['cat'] == c)}/{sum(r['cat'] == c for r in rs)}" for c in cats]
        speed = [r["tok_per_s"] for r in rs if r.get("tok_per_s")]
        lat = [r["latency_s"] for r in rs if r["cat"] != "long"]
        lines.append("| " + " | ".join([model, "on" if think else "off", *acc,
                     f"{sum(r['ok'] for r in rs)}/{len(rs)}",
                     f"{statistics.mean(speed):.1f}" if speed else "-",
                     f"{statistics.median(lat):.1f}" if lat else "-",
                     str(m["ram_gb"]), str(m["cold_s"])]) + " |")
    return "\n".join(lines)


def selftest():
    call = lambda name, **args: NS(function=NS(name=name, arguments=args))
    t = {"id": "t", "cat": "tools", "expect": {"tool": "detect_video", "args": {"path": "vtest.avi", "conf": 0.6}}}
    assert check(t, NS(tool_calls=[call("detect_video", path="workspace/vtest.avi", conf=0.6)], content=""))
    assert not check(t, NS(tool_calls=[call("detect_video", path="vtest.avi", conf=0.4)], content=""))
    assert not check(t, NS(tool_calls=None, content="вызову detect_video"))
    j = {"id": "j", "cat": "format", "expect": {"json": [{"len": 2}, {"sum": "count", "sum_eq": 3}]}}
    assert check(j, NS(tool_calls=None, content='```json\n[{"count": 1}, {"count": 2}]\n```'))
    h = {"id": "h", "cat": "hallucination", "expect": {"idk": True}}
    assert check(h, NS(tool_calls=None, content="Такой книги не существует."))
    assert not check(h, NS(tool_calls=None, content="Роман рассказывает о программисте."))
    assert check(h, NS(tool_calls=None, content="Не могу назвать дату, в моей базе нет этой информации."))
    h2 = {"id": "h2", "cat": "hallucination", "expect": {"idk": True, "not_regex": "известен"}}
    assert not check(h2, NS(tool_calls=None, content="Не могу назвать статьи. Он известен работами по физике."))
    c = {"id": "c", "cat": "russian", "expect": {"cyrillic": True}}
    assert check(c, NS(tool_calls=None, content="Контекстное окно — это объём текста в токенах."))
    print("selftest ok")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--models", nargs="+", default=MODELS)
    p.add_argument("--think", action="store_true")
    p.add_argument("--cats", nargs="+")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--report", action="store_true", help="собрать summary.md из всех прогонов")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.report:
        rows, meta = load_results()
        table = summarize(rescore(rows), meta)
        (OUT / "summary.md").write_text(table + "\n")
        return print(table)

    tasks = [json.loads(line) for line in TASKS.read_text().splitlines() if line.strip()]
    tasks = [t for t in tasks if not a.cats or t["cat"] in a.cats]
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    raw = OUT / f"bench-{stamp}{'-think' if a.think else ''}.jsonl"
    rows, meta = [], {}
    for model in a.models:
        cold, ram = warm_up(model)
        meta[(model, a.think)] = m = {"meta": True, "model": model, "think": a.think, "cold_s": cold, "ram_gb": ram}
        with raw.open("a") as f:
            f.write(json.dumps(m) + "\n")
        print(f"{model}: холодный старт {cold} с, RAM {ram} ГБ", flush=True)
        for t in tasks:
            row = run(model, t, a.think)
            rows.append(row)
            with raw.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"  {t['id']}: {'ok' if row['ok'] else 'FAIL'} {row['latency_s']} с", flush=True)
        try:
            client.generate(model=model, prompt="", keep_alive=0)
        except Exception as ex:
            print(f"  выгрузка {model} не удалась: {ex}", flush=True)

    table = summarize(rows, meta)
    (OUT / f"bench-{stamp}{'-think' if a.think else ''}.md").write_text(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
