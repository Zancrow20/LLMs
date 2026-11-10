import re
import time
import ollama

MODELS = ["qwen3:8b", "gemma4:e4b"]
CLIENT = ollama.Client(host="http://localhost:11434", trust_env=False)
# модели иногда вставляют эмодзи, в репозитории их быть не должно
NO_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]")

BENCHMARK_PROMPTS = [
    (
        "factology",
        "Назови столицу Австралии и год её основания как столицы в одном предложении.",
    ),
    (
        "arithmetic",
        "Вычисли точно без калькулятора: 47 * 83 + 129. Напиши только шаги и итоговое число.",
    ),
    (
        "code",
        "Напиши функцию на Python `is_prime(n: int) -> bool` с проверкой за O(sqrt(n)). Без лишнего текста.",
    ),
    (
        "translation",
        "Переведи на русский технически точно: 'Quantization reduces memory bandwidth pressure during autoregressive decoding.'",
    ),
    (
        "summarization",
        "Сожми в одно предложение (не более 20 слов): 'Трансформеры используют механизм внимания, позволяющий каждому токену напрямую взаимодействовать со всеми предыдущими токенами контекста, однако квадратичная сложность по длине последовательности требует использования KV-кэша при генерации.'",
    ),
    (
        "logic",
        "В комнате 3 лампочки, выключатели за дверью. В комнату можно войти один раз. Как определить, какой выключатель к какой лампочке относится? Ответь кратко в 3 шага.",
    ),
    (
        "russian_ml",
        "В чём разница между стадиями prefill и decode при инференсе LLM? Ответь в 2–3 предложениях.",
    ),
    (
        "hallucination_guard",
        "Расскажи сюжет романа Фёдора Достоевского «Хрустальный пароход» (1874).",
    ),
    (
        "code_security",
        "Найди уязвимость в строке Python: `cursor.execute(f\"SELECT * FROM users WHERE name = '{user}'\")` и напиши исправленную строку.",
    ),
]


def ask(prompt: str, model: str = "qwen3:8b") -> dict:
    t = time.perf_counter()
    first = None
    text = ""
    last = {}
    for chunk in CLIENT.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        think=False,
        options={"temperature": 0.0, "num_predict": 256},
    ):
        content = chunk["message"].get("content", "")
        if first is None and content:
            first = time.perf_counter() - t
        text += content
        last = chunk

    total_s = time.perf_counter() - t
    eval_count = last.get("eval_count", 0)
    eval_dur_ns = last.get("eval_duration", 1)
    load_dur_ns = last.get("load_duration", 0)
    prompt_eval_count = last.get("prompt_eval_count", 0)
    prompt_eval_dur_ns = last.get("prompt_eval_duration", 1)
    return {
        "model": model,
        "text": text.strip(),
        "ttft_s": first or total_s,
        "total_s": total_s,
        "load_ms": load_dur_ns / 1e6,
        "prompt_tokens": prompt_eval_count,
        "prefill_tok_s": prompt_eval_count / prompt_eval_dur_ns * 1e9 if prompt_eval_dur_ns else 0.0,
        "eval_tokens": eval_count,
        "tok_per_s": eval_count / eval_dur_ns * 1e9 if eval_dur_ns else 0.0,
    }


def evaluate_model(model: str) -> list[dict]:
    print(f"\n=== Модель: {model} ===")
    cold = ask("Привет! Ответь одним словом: готов.", model=model)
    print(
        f"[cold/warmup] load={cold['load_ms']:.1f} ms | "
        f"TTFT={cold['ttft_s']*1000:.0f} ms | {cold['tok_per_s']:.1f} tok/s"
    )
    results = []
    for category, prompt in BENCHMARK_PROMPTS:
        res = ask(prompt, model=model)
        res["category"] = category
        res["prompt"] = prompt
        results.append(res)
        snippet = res["text"].replace("\n", " ")[:140]
        print(
            f"[{category:19s}] TTFT={res['ttft_s']*1000:5.0f} ms | "
            f"decode={res['tok_per_s']:5.1f} tok/s | "
            f"tokens={res['eval_tokens']:3d} | {snippet}"
        )

    avg_ttft = sum(r["ttft_s"] for r in results) / len(results)
    avg_tps = sum(r["tok_per_s"] for r in results) / len(results)
    print(
        f"[Итого {model}] среднее TTFT={avg_ttft*1000:.0f} ms, "
        f"средняя скорость decode={avg_tps:.1f} tok/s"
    )
    return results


if __name__ == "__main__":
    import json
    from pathlib import Path

    out = Path(__file__).parent / "results" / "llm_local.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for m in MODELS:
            for r in evaluate_model(m):
                r["text"] = NO_EMOJI.sub("", r["text"])
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nПолные ответы: {out}")

