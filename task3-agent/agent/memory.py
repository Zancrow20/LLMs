"""Память агента (раздел 13):
1. Короткая: суммаризация истории сообщений при превышении 70% num_ctx (SqliteSaver в graph.py).
2. Долгая: темпоральный граф фактов с valid_at / invalid_at и инвалидацией противоречий (qwen3:8b JSON-схема + Neo4j / SQLite-граф) и сравнение с классическим RAG по эпизодам.
3. Процедурная: автоматическое обновление persona/USER.md при смене пользовательских предпочтений.
"""
import datetime as dt
import math
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import TypedDict

from langchain_core.messages import HumanMessage, RemoveMessage, SystemMessage
from langchain_core.tools import tool

from . import models

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "workspace"
DB_PATH = WORKSPACE / "long_memory.sqlite"
USER_MD = ROOT / "persona" / "USER.md"
MEMORY_WRITER_PROMPT = (ROOT / "prompts" / "memory_writer.md").read_text(encoding="utf-8").strip()

# Порог 70% от num_ctx=32768 (раздел 13.1 плана)
MAX_CTX_TOKENS = int(32768 * 0.70)


class FactItem(TypedDict):
    subject: str
    key: str
    value: str


class ExtractedEpisode(TypedDict):
    facts: list[FactItem]


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    p = db_path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS temporal_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            valid_at TEXT NOT NULL,
            invalid_at TEXT,
            thread_id TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    c.commit()
    return c


def estimate_tokens(messages: list) -> int:
    """Быстрая оценка числа токенов в истории (1 токен ~ 3.2 символа для кириллицы и кода)."""
    chars = sum(len(str(getattr(m, "content", ""))) for m in messages)
    return int(chars / 3.2)


def summarize_if_needed(messages: list, llm, max_tokens: int = MAX_CTX_TOKENS) -> list:
    """Если контекст сессии превысил 70% num_ctx — сжать старые реплики в одно SystemMessage."""
    if len(messages) <= 4 or estimate_tokens(messages) <= max_tokens:
        return []
    old_msgs = messages[:-2]
    transcript = "\n".join(f"{type(m).__name__}: {str(m.content)[:600]}" for m in old_msgs)
    prompt = (
        "Сожми историю предыдущего диалога в 3–4 коротких предложения, "
        "сохранив все проверенные имена файлов, числа, CWE/CVE и предпочтения пользователя. "
        "Без эмодзи.\n\n" + transcript
    )
    summary = llm.invoke([HumanMessage(prompt)]).content.strip()
    removals = [RemoveMessage(id=m.id) for m in old_msgs if getattr(m, "id", None)]
    return [*removals, SystemMessage(f"Сводка предыдущих сообщений сессии:\n{summary}")]


def _norm_key(subject: str, key: str) -> tuple[str, str]:
    s = subject.strip().lower()
    if s in {"пользователь", "юзер", "client", "user"}:
        s = "user"
    k = key.strip().lower().replace(" ", "_")
    if any(w in k for w in ("lang", "язык")):
        k = "report_language"
    elif any(w in k for w in ("style", "объем", "объём", "кратк", "length", "brevity")):
        k = "report_style"
    elif any(w in k for w in ("cwe", "format", "формат")):
        k = "security_format"
    return s, k


def _sync_user_md(db_path: Path | None = None) -> None:
    """Обновить процедурную память persona/USER.md по активным предпочтениям пользователя."""
    with _conn(db_path) as c:
        rows = c.execute(
            "SELECT key, value FROM temporal_edges WHERE subject = 'user' AND invalid_at IS NULL ORDER BY id"
        ).fetchall()
    prefs = {r["key"]: r["value"] for r in rows}
    lang = prefs.get("report_language", "на русском")
    style = prefs.get("report_style", "краткие: 2–4 предложения")
    extra = prefs.get("security_format", "")
    lines = [
        "# USER — предпочтения пользователя (правит memory_writer)",
        "",
        f"- По умолчанию отчёты {style}, если пользователь не попросил иначе.",
        f"- По умолчанию отчёты: {lang}. Язык явного указания пользователя в диалоге приоритетнее этой настройки; memory_writer обновляет этот файл, когда пользователь меняет предпочтение.",
    ]
    if extra:
        lines.append(f"- Дополнительное правило отчётов: {extra}.")
    USER_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add_facts(
    thread_id: str,
    facts: list[dict],
    episode_text: str = "",
    reference_time: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Записать извлечённые факты в темпоральный граф; противоречащие старые факты закрыть (invalid_at = now)."""
    now = reference_time or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    added = 0
    invalidated = 0
    user_updated = False

    with _conn(db_path) as c:
        if episode_text:
            c.execute(
                "INSERT INTO episodes (thread_id, body, created_at) VALUES (?, ?, ?)",
                (thread_id, episode_text, now),
            )
        for item in facts:
            subj, key = _norm_key(item.get("subject", ""), item.get("key", ""))
            val = str(item.get("value", "")).strip()
            if not subj or not key or not val:
                continue
            active = c.execute(
                "SELECT id, value FROM temporal_edges WHERE subject = ? AND key = ? AND invalid_at IS NULL",
                (subj, key),
            ).fetchall()
            if any(r["value"].lower() == val.lower() for r in active):
                continue
            if active:
                c.execute(
                    "UPDATE temporal_edges SET invalid_at = ? WHERE subject = ? AND key = ? AND invalid_at IS NULL",
                    (now, subj, key),
                )
                invalidated += len(active)
            c.execute(
                "INSERT INTO temporal_edges (subject, key, value, valid_at, invalid_at, thread_id) VALUES (?, ?, ?, ?, NULL, ?)",
                (subj, key, val, now, thread_id),
            )
            added += 1
            if subj == "user":
                user_updated = True
        c.commit()

    if user_updated and db_path is None:
        _sync_user_md()
    return {"added": added, "invalidated": invalidated, "timestamp": now}


def extract_and_store(
    thread_id: str,
    question: str,
    answer: str,
    reference_time: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Узел/задача memory_writer: извлечь факты через qwen3:8b (JSON-схема) и сохранить в темпоральный граф."""
    episode_text = f"Пользователь: {question}\nАссистент: {answer}"
    extractor = models.validator_llm().with_structured_output(ExtractedEpisode, method="json_schema")
    parsed = extractor.invoke([SystemMessage(MEMORY_WRITER_PROMPT), HumanMessage(episode_text)])
    facts = parsed.get("facts", []) if isinstance(parsed, dict) else []
    res = add_facts(thread_id, facts, episode_text=episode_text, reference_time=reference_time, db_path=db_path)
    res["facts"] = facts
    return res


def search_temporal(query: str, include_invalidated: bool = False, db_path: Path | None = None) -> list[dict]:
    """Поиск по темпоральному графу (по умолчанию только действующие факты с invalid_at IS NULL)."""
    tokens = [t.lower() for t in re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_.-]+", query) if len(t) >= 2]
    where = "" if include_invalidated else "WHERE invalid_at IS NULL"
    with _conn(db_path) as c:
        rows = [dict(r) for r in c.execute(f"SELECT * FROM temporal_edges {where} ORDER BY id DESC").fetchall()]
    if not tokens:
        return rows[:10]
    scored = []
    for r in rows:
        blob = f"{r['subject']} {r['key']} {r['value']}".lower()
        hits = sum(1 for t in tokens if t in blob)
        if hits > 0 or r["subject"] == "user":
            scored.append((hits, r["id"], r))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in scored[:10]] or rows[:5]


def search_classic_rag(query: str, k: int = 3, db_path: Path | None = None) -> list[dict]:
    """Классический чанковый RAG без темпоральной инвалидации (для эксперимента 13.3):
    ищет top-k эпизодов по косинусной близости TF-векторов без учёта отмены старых фактов.
    """
    q_tokens = Counter(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_.-]+", query.lower()))
    with _conn(db_path) as c:
        eps = [dict(r) for r in c.execute("SELECT * FROM episodes ORDER BY id ASC").fetchall()]
    scored = []
    q_norm = math.sqrt(sum(v * v for v in q_tokens.values())) or 1.0
    for ep in eps:
        d_tokens = Counter(re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9_.-]+", ep["body"].lower()))
        d_norm = math.sqrt(sum(v * v for v in d_tokens.values())) or 1.0
        dot = sum(q_tokens[w] * d_tokens[w] for w in q_tokens if w in d_tokens)
        sim = round(dot / (q_norm * d_norm), 3)
        scored.append({**ep, "similarity": sim})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:k]


@tool
def memory_search(query: str) -> list[dict]:
    """Поиск действующих фактов о пользователе и ранее проверенных файлах в долгой темпоральной памяти."""
    return search_temporal(query, include_invalidated=False)

