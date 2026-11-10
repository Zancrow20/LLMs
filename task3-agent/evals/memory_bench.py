"""Оценка системы памяти (раздел 13):
1. Короткая память: сохранность по thread_id в SqliteSaver и суммаризация при > 70% num_ctx.
2. Долгая память (qwen3:8b JSON-схема + темпоральный граф): точность извлечения фактов на 20 сессиях.
3. Эксперимент 13.3: сравнение классического чанкового RAG и темпорального графа (valid_at / invalid_at) при смене предпочтения пользователя в 3 сессиях.

Запуск из task3-agent/:
  PYTHONPATH=. uv run python evals/memory_bench.py
"""
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agent import graph, memory, models

OUT = Path(__file__).parent / "results" / "memory_bench.json"

SESSIONS_20 = [
    ("s01", "Запомни: все отчёты аудита пиши на английском языке.", "Принято, язык отчётов установлен: английский.", "user", "report_language", "английский", False),
    ("s02", "Теперь пиши отчёты на русском языке.", "Хорошо, теперь пишу отчёты на русском.", "user", "report_language", "русский", True),
    ("s03", "Делай отчёты максимально подробными.", "Учёл, отчёты будут подробными.", "user", "report_style", "подробн", False),
    ("s04", "Больше не пиши длинно, пиши кратко в 2-3 строки.", "Понял, перехожу на краткий формат.", "user", "report_style", "кратк", True),
    ("s05", "Всегда указывай идентификатор CWE для каждой уязвимости.", "Запомнил: всегда указывать CWE.", "user", "security_format", "cwe", False),
    ("s06", "В файле vuln_app.py сколько уязвимостей?", "В файле vuln_app.py найдено 6 уязвимостей (2 CRITICAL, 1 HIGH, 2 MEDIUM, 1 LOW).", "vuln_app.py", "total_findings", "6", False),
    ("s07", "После патча в vuln_app.py сколько осталось уязвимостей?", "После исправления в vuln_app.py осталось 2 уязвимости.", "vuln_app.py", "total_findings", "2", True),
    ("s08", "Сколько секретов нашли в vuln_app.py?", "В файле vuln_app.py обнаружено 3 секрета (CWE-798).", "vuln_app.py", "total_secrets", "3", False),
    ("s09", "Секреты из vuln_app.py вынесли в .env, сколько теперь секретов в файле?", "Теперь в vuln_app.py 0 секретов.", "vuln_app.py", "total_secrets", "0", True),
    ("s10", "Проверь frontend_widget.ts.", "В файле frontend_widget.ts найдено 3 уязвимости: CWE-79, CWE-94 и CWE-295.", "frontend_widget.ts", "total_findings", "3", False),
    ("s11", "Какие основные CWE в UserService.cs?", "В UserService.cs основные уязвимости: CWE-89, CWE-502, CWE-327.", "userservice.cs", "main_vulns", "cwe-89", False),
    ("s12", "Сколько уязвимых пакетов в requirements_vuln.txt?", "В requirements_vuln.txt найдено 4 уязвимых пакета с CVE.", "requirements_vuln.txt", "vulnerable_count", "4", False),
    ("s13", "Мы обновили requests и flask в requirements_vuln.txt, сколько уязвимых пакетов осталось?", "В requirements_vuln.txt осталось 2 уязвимых пакета.", "requirements_vuln.txt", "vulnerable_count", "2", True),
    ("s14", "Что в логе auth_access.csv?", "В auth_access.csv зафиксирована аномалия: 6 ошибок 401 с IP 185.220.101.42.", "auth_access.csv", "log_anomalies", "185.220.101.42", False),
    ("s15", "В модуле payment.go сколько находок?", "В файле payment.go найдено 1 срабатывание (CWE-89).", "payment.go", "total_findings", "1", False),
    ("s16", "В Dockerfile какие проблемы?", "В Dockerfile найдено 2 проблемы безопасности (USER root, CWE-250).", "dockerfile", "total_findings", "2", False),
    ("s17", "В package.json сколько уязвимых зависимостей?", "В package.json обнаружено 3 уязвимых пакета.", "package.json", "vulnerable_count", "3", False),
    ("s18", "После обновления npm-пакетов в package.json сколько уязвимостей?", "В package.json осталось 0 уязвимых пакетов.", "package.json", "vulnerable_count", "0", True),
    ("s19", "Привет! Что ты умеешь делать?", "Я локальный аудитор безопасности: проверяю код, секреты, зависимости и логи.", None, None, None, False),
    ("s20", "Спасибо за справку!", "Пожалуйста, обращайтесь при необходимости проверки файлов.", None, None, None, False),
]


def test_short_memory_and_summarization(tmp_dir: Path) -> dict:
    """1. Проверить сохранение истории в SqliteSaver между перезапусками и сжатие при > 70% num_ctx."""
    cp_file = tmp_dir / "short_test.sqlite"
    cfg = {"configurable": {"thread_id": "persist-check"}}

    # Сессия до «перезапуска»: сохраняем checkpoint
    conn1 = sqlite3.connect(str(cp_file), check_same_thread=False)
    cp1 = SqliteSaver(conn1)
    g1 = graph.build().compile(checkpointer=cp1)
    g1.update_state(cfg, {"messages": [HumanMessage("Запомни кодовое слово: ОРИОН-42", id="m1"),
                                       AIMessage("Запомнил кодовое слово ОРИОН-42.", id="m2")]})
    conn1.close()

    # Новый экземпляр SqliteSaver и графа («после перезапуска процесса»)
    conn2 = sqlite3.connect(str(cp_file), check_same_thread=False)
    cp2 = SqliteSaver(conn2)
    g2 = graph.build().compile(checkpointer=cp2)
    restored = g2.get_state(cfg).values.get("messages", [])
    conn2.close()
    survived = len(restored) == 2 and "ОРИОН-42" in restored[0].content

    # Суммаризация при превышении бюджета токенов (порог искусственно снижен до 50 токенов для теста)
    long_msgs = [
        HumanMessage("Провели аудит vuln_app.py, нашли 6 уязвимостей CWE-89 и CWE-78." * 3, id="h1"),
        AIMessage("Подтверждаю: в vuln_app.py 6 находок, включая SQLi на строке 14." * 3, id="a1"),
        HumanMessage("Также проверили requirements_vuln.txt, там 4 CVE (CVE-2020-14343)." * 3, id="h2"),
        AIMessage("В requirements_vuln.txt 4 уязвимых пакета и 2 незафиксированных." * 3, id="a2"),
        HumanMessage("Какой итог по двум файлам?", id="h3"),
    ]
    ops = memory.summarize_if_needed(long_msgs, models.llm("main"), max_tokens=50)
    summary_text = ops[-1].content if ops else ""
    return {
        "restart_survived": survived,
        "restored_messages": len(restored),
        "summarization_triggered": len(ops) > 0,
        "removed_old_messages": len(ops) - 1 if ops else 0,
        "summary_preview": summary_text[:240],
    }


def test_20_sessions_extraction(tmp_dir: Path) -> dict:
    """2. Прогнать memory_writer (qwen3:8b) на 20 сессиях: измерить точность извлечения и инвалидации."""
    db_file = tmp_dir / "long_20.sqlite"
    matched = 0
    invalidations_ok = 0
    expected_invalidations = 0
    details = []

    for idx, (sid, q, a, exp_subj, exp_key, exp_val_sub, exp_inv) in enumerate(SESSIONS_20, start=1):
        t_iso = f"2026-11-10T12:{idx:02d}:00Z"
        t0 = time.perf_counter()
        res = memory.extract_and_store(sid, q, a, reference_time=t_iso, db_path=db_file)
        dt_s = round(time.perf_counter() - t0, 2)

        if exp_subj is None:
            ok = len(res["facts"]) == 0
        else:
            active = memory.search_temporal(f"{exp_subj} {exp_key}", db_path=db_file)
            ok = any(
                r["subject"] == exp_subj and exp_val_sub.lower() in r["value"].lower()
                for r in active
            )
            if exp_inv:
                expected_invalidations += 1
                if res["invalidated"] >= 1:
                    invalidations_ok += 1

        matched += int(ok)
        mark = "OK" if ok else "MISS"
        print(f"[{mark}] {sid} ({dt_s}s) added={res['added']} inv={res['invalidated']} facts={res['facts']}")
        details.append({"id": sid, "ok": ok, "latency_s": dt_s, "added": res["added"], "invalidated": res["invalidated"], "facts": res["facts"]})

    all_edges = memory.search_temporal("", include_invalidated=True, db_path=db_file)
    stale_in_active = sum(1 for r in memory.search_temporal("", include_invalidated=False, db_path=db_file) if r["invalid_at"] is not None)
    return {
        "total_sessions": len(SESSIONS_20),
        "matched_sessions": matched,
        "accuracy": round(matched / len(SESSIONS_20), 3),
        "invalidations_expected": expected_invalidations,
        "invalidations_triggered": invalidations_ok,
        "stale_facts_in_active_search": stale_in_active,
        "total_edges_stored": len(all_edges),
        "details": details,
    }


def test_rag_vs_temporal_experiment(tmp_dir: Path) -> dict:
    """3. Эксперимент 13.3: 3 сессии со сменой языка отчётов — классический RAG против темпорального графа."""
    db_file = tmp_dir / "exp_13_3.sqlite"
    memory.extract_and_store(
        "s1",
        "Отчёты пиши на английском.",
        "Принято: отчёты буду писать на английском языке.",
        reference_time="2026-11-10T10:00:00Z",
        db_path=db_file,
    )
    memory.extract_and_store(
        "s2",
        "Теперь пиши на русском.",
        "Хорошо, теперь отчёты пишу на русском языке.",
        reference_time="2026-11-10T11:00:00Z",
        db_path=db_file,
    )

    question = "На каком языке писать отчёт?"
    rag_chunks = memory.search_classic_rag(question, k=2, db_path=db_file)
    temporal_active = memory.search_temporal("report_language язык отчётов", include_invalidated=False, db_path=db_file)
    temporal_all = memory.search_temporal("report_language язык отчётов", include_invalidated=True, db_path=db_file)

    llm = models.llm("main")
    rag_ctx = "\n---\n".join(f"[{c['created_at']}] {c['body']}" for c in rag_chunks)
    rag_answer = llm.invoke([
        HumanMessage(f"Контекст из векторного/чанкового RAG:\n{rag_ctx}\n\nВопрос: {question} Ответь одним предложением.")
    ]).content.strip()

    temp_ctx = "\n".join(
        f"- {f['subject']}.{f['key']} = {f['value']} (valid_at={f['valid_at']}, invalid_at={f['invalid_at']})"
        for f in temporal_active
    )
    temp_answer = llm.invoke([
        HumanMessage(f"Действующие факты из темпорального графа (invalid_at=None):\n{temp_ctx}\n\nВопрос: {question} Ответь одним предложением.")
    ]).content.strip()

    return {
        "question": question,
        "rag_retrieved_chunks": rag_chunks,
        "rag_has_stale_fact": any("английск" in c["body"].lower() for c in rag_chunks),
        "rag_answer": rag_answer,
        "temporal_active_facts": temporal_active,
        "temporal_history_all_edges": temporal_all,
        "temporal_has_stale_fact": any("английск" in f["value"].lower() for f in temporal_active),
        "temporal_answer": temp_answer,
    }


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        print("=== 1. Короткая память и суммаризация (>70% num_ctx) ===")
        short_res = test_short_memory_and_summarization(tmp)
        print(json.dumps(short_res, ensure_ascii=False, indent=2))

        print("\n=== 2. Извлечение и обновление фактов на 20 сессиях (qwen3:8b) ===")
        ext_res = test_20_sessions_extraction(tmp)
        print(
            f"Точность на 20 сессиях: {ext_res['matched_sessions']}/{ext_res['total_sessions']} "
            f"({ext_res['accuracy']*100:.1f}%), инвалидаций: {ext_res['invalidations_triggered']}/{ext_res['invalidations_expected']}"
        )

        print("\n=== 3. Эксперимент 13.3: Классический RAG против Темпорального графа ===")
        exp_res = test_rag_vs_temporal_experiment(tmp)
        print(json.dumps(exp_res, ensure_ascii=False, indent=2))

    report = {"short_memory": short_res, "sessions_20": ext_res, "experiment_13_3": exp_res}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

