"""Прямой вызов 4 AppSec-тулов MCP из песочницы (mcp 2.x: ответ — список контента)."""
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
    host = "host.docker.internal" if os.environ.get("SANDBOX_NAME") else "localhost"
    client = MultiServerMCPClient(
        {"sec": {"url": f"http://{host}:8765/mcp", "transport": "streamable_http"}})
    tools = {t.name: t for t in await client.get_tools()}
    print("тулы:", sorted(tools))

    r = unwrap(await tools["scan_code"].ainvoke({"path": "vuln_app.py"}))
    print("scan_code (vuln_app.py):", {k: r[k] for k in ("language", "total_findings", "by_severity", "mean_conf")})
    r_ts = unwrap(await tools["scan_code"].ainvoke({"path": "frontend_widget.ts"}))
    print("scan_code (frontend_widget.ts):", {k: r_ts[k] for k in ("language", "total_findings", "by_severity")})
    r = unwrap(await tools["scan_secrets"].ainvoke({"path": "vuln_app.py"}))
    print("scan_secrets:", {k: r[k] for k in ("total_secrets", "mean_conf")})
    r = unwrap(await tools["check_dependencies"].ainvoke({"path": "requirements_vuln.txt"}))
    print("check_dependencies:", {k: r[k] for k in ("ecosystem", "total_packages", "vulnerable_count", "unpinned_count")})
    r = unwrap(await tools["read_code_slice"].ainvoke({"path": "UserService.cs", "start_line": 1, "end_line": 20}))
    print("read_code_slice:", {k: r[k] for k in ("language", "start_line", "end_line", "total_lines")})


asyncio.run(main())
