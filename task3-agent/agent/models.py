"""Клиенты LLM, decision-модель и выбор модели по сложности задачи."""
import os

import httpx
from langchain_ollama import ChatOllama

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DECIDER = "strands-decider"
MAIN, HEAVY, VALIDATOR = "gemma4:e4b", "gpt-oss:20b", "qwen3:8b"
HEAVY_THRESHOLD = 0.85

# trust_env=False: в окружении HTTP_PROXY, через него долгие ответы Ollama обрываются
_http = httpx.Client(trust_env=False, timeout=60)
_ollama = {"base_url": OLLAMA, "client_kwargs": {"trust_env": False}, "keep_alive": "30m", "temperature": 0}

ROUTE = {
    "hard": {"type": "noul", "instructions": "Задача требует нескольких шагов, рассуждения или кода"},
    "kind": {"type": "choice", "instructions": "Какой агент нужен",
             "criteria": {"media": "анализ видео, аудио или картинки",
                          "analyst": "расчёты по таблице или код",
                          "chat": "вопрос без файла"}},
}


def decide(state: str, questions: dict) -> dict:
    """Вопросы к decision-модели. Контекст 4096 токенов, поэтому state обрезается."""
    r = _http.post(f"{OLLAMA}/v1/systemone", json={"model": DECIDER, "state": state[:6000], "questions": questions})
    r.raise_for_status()
    return r.json()["answers"]


def pick_tier(hard: float) -> str:
    """Уровень LLM по ответу decision-модели: "main" или "heavy".

    Уверенность в типе задачи не учитывается: в серой зоне hard она не отделяет сложные задачи от простых.
    """
    if hard >= HEAVY_THRESHOLD:
        return "heavy"
    return "main"


def llm(tier: str) -> ChatOllama:
    # без num_predict модель может зациклиться: gpt-oss с reasoning="high" выдал 15 тыс. токенов за 10 мин
    if tier == "heavy":
        return ChatOllama(model=HEAVY, reasoning="high", num_ctx=32768, num_predict=8192, **_ollama)
    return ChatOllama(model=MAIN, reasoning=False, num_ctx=32768, num_predict=2048, **_ollama)


def validator_llm() -> ChatOllama:
    # GGUF-модель: только она в этой сборке Ollama держит JSON-схему; 8192 — чтобы три модели влезли в 17.8 ГБ GPU
    return ChatOllama(model=VALIDATOR, reasoning=False, num_ctx=8192, **_ollama)
