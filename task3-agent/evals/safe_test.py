"""Путь вне workspace/ должен давать ошибку (тест safe() через MCP)."""
import asyncio
import os

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,host.docker.internal")
from langchain_mcp_adapters.client import MultiServerMCPClient


async def main():
    host = "host.docker.internal" if os.environ.get("SANDBOX_NAME") else "localhost"
    c = MultiServerMCPClient({"sec": {"url": f"http://{host}:8765/mcp", "transport": "streamable_http"}})
    tools = {t.name: t for t in await c.get_tools()}
    try:
        r = await tools["scan_code"].ainvoke({"path": "../pyproject.toml"})
        print("результат:", str(r)[:120])
    except Exception as e:
        print("ожидаемая ошибка:", str(e)[:140])


asyncio.run(main())
