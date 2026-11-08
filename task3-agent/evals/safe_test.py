"""Путь вне workspace/ должен давать ошибку (тест safe() через MCP)."""
import asyncio, os
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,host.docker.internal")
from langchain_mcp_adapters.client import MultiServerMCPClient

async def main():
    c = MultiServerMCPClient({"ml": {"url": "http://host.docker.internal:8765/mcp", "transport": "streamable_http"}})
    tools = {t.name: t for t in await c.get_tools()}
    try:
        r = await tools["classify_image"].ainvoke({"path": "../task1-ml-models/data/ru_tts.wav"})
        print("результат:", str(r)[:120])
    except Exception as e:
        print("ожидаемая ошибка:", str(e)[:140])

asyncio.run(main())
