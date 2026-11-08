"""Прямой вызов 4 тулов MCP из песочницы (mcp 2.x: ответ — список контента)."""
import asyncio
import json
import os

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,host.docker.internal")
from langchain_mcp_adapters.client import MultiServerMCPClient


def unwrap(r):
    """Достать данные из ответа тула: [{type: text, text: json}] -> dict/str."""
    if isinstance(r, list) and r and r[0].get("type") == "text":
        body = r[0]["text"]
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body
    return r


async def main():
    client = MultiServerMCPClient(
        {"ml": {"url": "http://host.docker.internal:8765/mcp", "transport": "streamable_http"}})
    tools = {t.name: t for t in await client.get_tools()}
    print("тулы:", sorted(tools))

    r = unwrap(await tools["sentiment"].ainvoke({"text": "I loved this movie!"}))
    print("sentiment:", r)
    r = unwrap(await tools["classify_image"].ainvoke({"path": "photo.jpg", "k": 2}))
    print("classify_image:", r)
    r = unwrap(await tools["transcribe"].ainvoke({"path": "ru_tts.wav"}))
    print("transcribe:", str(r)[:90])
    r = unwrap(await tools["detect_video"].ainvoke({"path": "vtest.avi", "track": True, "max_frames": 60}))
    print("detect_video:", {k: r[k] for k in ("frames", "det_per_frame", "unique")})

asyncio.run(main())
