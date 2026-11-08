"""Тулы агентов. MCP-тулы для media подключаются в разделе 10."""
import re
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool
from langgraph.types import interrupt

from . import models

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
RISK = {"risky": {"type": "noul",
                  "instructions": "Код удаляет или перезаписывает файлы, обращается к сети или читает файлы вне workspace"}}
GATE_THRESHOLD = 0.5
# Decision-модель недооценивает риск в коде: shutil.rmtree получил 0.13, а русское
# описание удаления — 0.75 (JOURNAL.md, 2026-11-08). Статические паттерны ловят
# детерминированные операции, модель — смысловые варианты.
RISKY_CODE = re.compile(
    r"os\.(remove|unlink|rmdir)|shutil\.rmtree|os\.system|subprocess"
    r"|requests\.|urllib|urlopen|socket\.", re.I)


def gate(code: str) -> float:
    """Итоговый риск кода: 1.0 по статическому паттерну, иначе оценка модели."""
    if RISKY_CODE.search(code):
        return 1.0
    return models.decide(code, RISK)["risky"]["noul"]


@tool
def run_python(code: str) -> str:
    """Выполнить Python-код. Файлы пользователя лежат в workspace/."""
    risk = gate(code)
    # при resume граф перезапускает тул с начала, interrupt возвращает ответ пользователя
    if risk > GATE_THRESHOLD and not interrupt({"code": code, "risk": round(risk, 2)}):
        return "Пользователь запретил выполнять этот код."
    # сам агент работает в microVM sbx, код исполняется в той же изолированной VM
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=WORKSPACE,
                           capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "timeout 30 s"
    out = (r.stdout + r.stderr)[-4000:]
    return out if out.strip() else "код выполнен, вывод пуст"


def _to_sync(t):
    """MCP-тулы адаптера только async, а граф зовёт их синхронно — мост."""
    import asyncio

    from langchain_core.tools import StructuredTool

    def call(**kwargs):
        return asyncio.run(t.coroutine(**kwargs))

    return StructuredTool(name=t.name, description=t.description,
                          args_schema=t.args_schema, func=call)


def _load_media_tools() -> list:
    """MCP-тулы задания 1 с хоста. В песочнице — через host.docker.internal."""
    import asyncio
    import os

    # локальные адреса мимо HTTP_PROXY (на хосте задан, в песочнице нет)
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,host.docker.internal")
    host = "host.docker.internal" if os.environ.get("SANDBOX_NAME") else "localhost"
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"ml": {"url": f"http://{host}:8765/mcp", "transport": "streamable_http"}})
    return [_to_sync(t) for t in asyncio.run(client.get_tools())]


try:
    MEDIA_TOOLS = _load_media_tools()
    print(f"[tools] MCP: {len(MEDIA_TOOLS)} тулов")
except Exception as e:  # сервер не поднят — media-агент остаётся без ML-тулов
    print(f"[tools] MCP недоступен: {e}")
    MEDIA_TOOLS = []

ANALYST_TOOLS = [run_python]
