"""Тулы агентов. MCP-тулы для scanner подключаются с хоста по streamable-http."""
import json
import re
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool
from langgraph.types import interrupt

from . import memory, models

WORKSPACE = Path(__file__).resolve().parents[1] / "workspace"
SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"
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
    """Выполнить Python-код. Рабочая папка — уже внутри workspace/, открывай файлы без префикса workspace/ (например, open('auth_access.csv'))."""
    risk = gate(code)
    # при resume граф перезапускает тул с начала, interrupt возвращает ответ пользователя
    if risk > GATE_THRESHOLD and not interrupt({"code": code, "risk": round(risk, 2)}):
        return "Пользователь запретил выполнять этот код."
    # если модель всё же написала open('workspace/file.csv'), нормализуем путь
    code = code.replace("workspace/", "")
    # сам агент работает в microVM sbx, код исполняется в той же изолированной VM
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=WORKSPACE,
                           capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "timeout 30 s"
    out = (r.stdout + r.stderr)[-4000:]
    return out if out.strip() else "код выполнен, вывод пуст"


def _frontmatter(md: Path) -> dict:
    """name и description из frontmatter SKILL.md."""
    head = md.read_text().split("---", 2)
    out = {}
    if len(head) >= 3:
        for line in head[1].strip().splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                out[key.strip()] = value.strip()
    return out


def skill_catalog() -> str:
    """Каталог скиллов для системного промпта: только name и description."""
    lines = []
    for md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        meta = _frontmatter(md)
        if meta.get("name") and meta.get("description"):
            lines.append(f"- {meta['name']}: {meta['description']}")
    return "\n".join(lines) or "Скиллов нет."


def _skill_names() -> list[str]:
    return sorted(p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md"))


@tool
def load_skill(name: str) -> str:
    """Загрузить тело скилла по имени из каталога скиллов."""
    body = (SKILLS_DIR / name).resolve() / "SKILL.md"
    if not body.is_relative_to(SKILLS_DIR) or not body.exists():
        return f"Скилл «{name}» не найден. Доступны: {', '.join(_skill_names())}"
    # тело без frontmatter: агенту нужны шаги, каталог он уже видел в промпте
    parts = body.read_text().split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else body.read_text().strip()


def _unwrap_mcp(r):
    """mcp-ответ может быть [{type: text, text: json}] и/или вложен в списки.

    Разворачиваем, иначе валидатор не сверит числа с выводом тула.
    """
    for _ in range(3):
        if isinstance(r, list) and r and isinstance(r[0], dict) and r[0].get("type") == "text":
            try:
                return json.loads(r[0]["text"])
            except (json.JSONDecodeError, TypeError):
                return r[0]["text"]
        if isinstance(r, list) and len(r) == 1:
            r = r[0]
            continue
        break
    return r


def _to_sync(t):
    """MCP-тулы адаптера только async, а граф зовёт их синхронно — мост."""
    import asyncio

    from langchain_core.tools import StructuredTool

    def call(**kwargs):
        r = asyncio.run(t.coroutine(**kwargs))
        return _unwrap_mcp(r)

    return StructuredTool(name=t.name, description=t.description,
                          args_schema=t.args_schema, func=call)


def _load_scanner_tools() -> list:
    """MCP-тулы AppSec-сканера с хоста. В песочнице — через host.docker.internal."""
    import asyncio
    import os

    # локальные адреса мимо HTTP_PROXY (на хосте задан, в песочнице нет)
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,host.docker.internal")
    host = "host.docker.internal" if os.environ.get("SANDBOX_NAME") else "localhost"
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"sec": {"url": f"http://{host}:8765/mcp", "transport": "streamable_http"}})
    return [_to_sync(t) for t in asyncio.run(client.get_tools())]


try:
    SCANNER_TOOLS = _load_scanner_tools()
    print(f"[tools] MCP: {len(SCANNER_TOOLS)} тулов")
except Exception as e:  # сервер не поднят — scanner-агент остаётся без MCP-тулов
    print(f"[tools] MCP недоступен: {e}")
    SCANNER_TOOLS = []

MEDIA_TOOLS = SCANNER_TOOLS
ANALYST_TOOLS = [run_python]
SKILL_TOOL = load_skill
MEMORY_TOOL = memory.memory_search
