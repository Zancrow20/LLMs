"""Оценка валидатора (раздел 12): 20 верных и 20 ошибочных черновиков + проверка цикла повтора.

Запуск из task3-agent/:
  PYTHONPATH=. uv run python evals/validator_bench.py
"""
import json
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from agent import graph

HERE = Path(__file__).parent
TASKS = HERE / "validator_tasks.jsonl"
OUT = HERE / "results" / "validator_bench.jsonl"


def valid_schema(v: dict) -> bool:
    return (isinstance(v, dict)
            and v.get("verdict") in ("pass", "fail")
            and isinstance(v.get("unsupported_claims"), list)
            and isinstance(v.get("fix_hint"), str))


def test_retry_and_fallback() -> dict:
    """Проверить 1) исправление черновика после fail и 2) пометку после MAX_RETRIES отказов."""
    evidence = [
        '{"file": "vuln_app.py", "language": "python", "total_findings": 2, '
        '"findings": [{"line": 14, "cwe": "CWE-89", "severity": "HIGH", "title": "SQL-инъекция"}, '
        '{"line": 20, "cwe": "CWE-78", "severity": "CRITICAL", "title": "Инъекция команд ОС"}]}'
    ]
    question = "Какие уязвимости найдены в vuln_app.py?"
    bad_draft = "В файле vuln_app.py найдено 9 уязвимостей, включая переполнение буфера CWE-120 на строке 55."

    # 1. Валидатор отклоняет неверный черновик -> оркестратор исправляет -> валидатор принимает
    s1 = {
        "messages": [HumanMessage(question), AIMessage(bad_draft)],
        "tier": "main",
        "kind": "scanner",
        "route": {},
        "evidence": evidence,
        "retries": 0,
        "verdict": {},
    }
    v1 = graph.validate(s1)
    s1.update(v1)
    fixed_msg = graph.orchestrate(s1)["messages"][-1]
    s1["messages"].append(fixed_msg)
    v2 = graph.validate(s1)

    # 2. Превышение лимита повторов (retries == MAX_RETRIES перед вызовом validate на ошибочном ответе)
    s2 = {
        "messages": [HumanMessage(question), AIMessage(bad_draft, id="draft-unverified")],
        "tier": "main",
        "kind": "scanner",
        "route": {},
        "evidence": evidence,
        "retries": graph.MAX_RETRIES,
        "verdict": {},
    }
    v_limit = graph.validate(s2)
    after_edge = graph.after_validation({**s2, **v_limit})
    fallback_text = v_limit.get("messages", [AIMessage("")])[-1].content

    return {
        "retry_initial_verdict": v1["verdict"]["verdict"],
        "retry_fixed_answer": fixed_msg.content,
        "retry_final_verdict": v2["verdict"]["verdict"],
        "retry_count": v2["retries"],
        "fallback_retries": v_limit["retries"],
        "fallback_edge": after_edge,
        "fallback_marked": "(Ответ не проверен валидатором.)" in fallback_text,
    }


def main():
    tasks = [json.loads(line) for line in TASKS.read_text().splitlines() if line.strip()]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    tp = fp = fn = tn = json_ok = 0

    for t in tasks:
        t0 = time.perf_counter()
        try:
            v = graph.check_answer(t["question"], t["evidence"], t["draft"])
            schema_ok = valid_schema(v)
        except Exception as e:
            v = {"verdict": "error", "unsupported_claims": [], "fix_hint": str(e)}
            schema_ok = False
        dt = round(time.perf_counter() - t0, 2)
        json_ok += schema_ok
        pred = v.get("verdict")
        exp = t["expect"]
        # Положительный класс для валидатора — обнаружение ошибки ("fail")
        if exp == "fail" and pred == "fail":
            tp += 1
        elif exp == "pass" and pred == "fail":
            fp += 1
        elif exp == "fail" and pred == "pass":
            fn += 1
        elif exp == "pass" and pred == "pass":
            tn += 1

        mark = "OK" if pred == exp else "MISS"
        print(f"[{mark}] {t['id']} exp={exp} pred={pred} ({dt}s) claims={v.get('unsupported_claims')}")
        rows.append({**t, "pred": pred, "schema_ok": schema_ok, "latency_s": dt, "verdict_obj": v})

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    acc = (tp + tn) / len(tasks)

    print(f"\nJSON valid: {json_ok}/{len(tasks)} ({100 * json_ok / len(tasks):.0f}%)")
    print(f"TP={tp} FP={fp} FN={fn} TN={tn} | Accuracy={acc:.3f}")
    print(f"Precision (fail)={precision:.3f} | Recall (fail)={recall:.3f} | F1={f1:.3f}")

    loop_res = test_retry_and_fallback()
    print("\nПроверка цикла повтора и fallback:")
    print(json.dumps(loop_res, ensure_ascii=False, indent=2))

    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


if __name__ == "__main__":
    main()
