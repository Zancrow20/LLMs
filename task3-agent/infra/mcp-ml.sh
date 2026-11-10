#!/bin/bash
# MCP-сервер AppSec-тулов (sec-tools). Работает на хосте.
# Запускать из корня репо или как есть: скрипт сам переходит в нужный каталог.
set -e
cd "$(dirname "$0")/../.."

pkill -f "task3-agent/mcp_ml/server.py" 2>/dev/null || true
sleep 1
nohup uv run python "$(pwd)/task3-agent/mcp_ml/server.py" > /tmp/mcp_ml.log 2>&1 &
echo "pid $!"
for i in $(seq 1 15); do
  code=$(curl -s --noproxy '*' -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8765/mcp \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}},"id":1}' || true)
  [ "$code" = "200" ] && { echo "MCP на :8765 готов (${i} с)"; exit 0; }
  sleep 1
done
echo "сервер не поднялся, лог:"; tail -5 /tmp/mcp_ml.log; exit 1
