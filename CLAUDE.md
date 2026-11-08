# LLMs: задания курса

Репозиторий с заданиями курса. Каждое задание — своя папка: `task1-ml-models/`, `task3-agent/`. Планы лежат в `docs/plans/`, но `docs/` исключена из git правилом `*docs` в `.gitignore`.

## Правила

- Не писать эмодзи в коде, промптах, логах, README и отчётах. Преподаватель считает эмодзи в репо редфлагом.
- Комментарии в коде — короткие. Объяснения — в `.md`.
- Зависимости ставить через `uv add`. Python 3.12.
- Агент задания 3 работает в песочнице `sbx`. Код агента не запускать на хосте вне песочницы, кроме MCP-сервера и бенчмарка LLM.
- Перед коммитом в `task3-agent/` запускать evals: `uv run task3-agent/evals/run_evals.py` (появится в разделе 14 плана).
- Ход работы и проблемы записывать в `task3-agent/JOURNAL.md`.

## Задание 3

- План: `docs/plans/task3-agent.md`. Вопросы к защите: `docs/plans/task3-agent-questions.md`.
- Диаграммы: `task3-agent/diagrams/workspace.dsl` (Structurizr DSL) и `task3-agent/diagrams/diagrams.md` (Mermaid).
- Модели крутятся в Ollama на хосте: `localhost:11434`.
