#!/bin/bash
# Kit раздела 9: песочница sbx с агентом. Идемпотентен.
# Даёт: microVM с именем agent, workspace task3-agent, allowlist сети,
# отдельный venv внутри VM (host venv не трогается).
set -e
cd "$(dirname "$0")/.."

SBX_NAME=agent

docker info >/dev/null 2>&1 || { open -a Docker; sleep 20; }

# allowlist: политика deny-all должна быть инициализирована, правила идемпотентны
sbx policy ls >/dev/null 2>&1 || sbx policy init deny-all
sbx policy allow network "host.docker.internal:11434,127.0.0.1:11434,localhost:11434" >/dev/null 2>&1 || true
sbx policy allow network "host.docker.internal:8765,127.0.0.1:8765,localhost:8765" >/dev/null 2>&1 || true
sbx policy allow network "host.docker.internal:3000,127.0.0.1:3000,localhost:3000" >/dev/null 2>&1 || true
sbx policy allow network "host.docker.internal:4318,127.0.0.1:4318,localhost:4318" >/dev/null 2>&1 || true
sbx policy allow network "pypi.org,files.pythonhosted.org" >/dev/null 2>&1 || true

# песочница: workspace монтируется по абсолютному пути (virtiofs)
sbx ls 2>/dev/null | grep -q "^$SBX_NAME" || \
  sbx create shell "$(pwd)" --name "$SBX_NAME"

# venv внутри VM: .venv в смонтированной папке принадлежит хосту (macOS)
sbx exec "$SBX_NAME" -- sh -c \
  'cd /Users/zancrow/GitHub/LLMs/task3-agent && UV_PROJECT_ENVIRONMENT=/home/agent/.venv-sbx uv sync 2>&1 | tail -1'

echo
echo "Запуск агента в песочнице:"
echo "  sbx exec $SBX_NAME -- sh -c 'cd /Users/zancrow/GitHub/LLMs/task3-agent && \"
  echo "  UV_PROJECT_ENVIRONMENT=/home/agent/.venv-sbx OLLAMA_HOST=http://host.docker.internal:11434 \\"
echo "  uv run python -m agent.graph \"вопрос\"'"
