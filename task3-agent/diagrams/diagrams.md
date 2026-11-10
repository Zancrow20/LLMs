# Диаграммы: аудитор безопасности (Security Reviewer)

Диаграммы системы лежат в двух файлах:

- [workspace.dsl](workspace.dsl) — C4-модель в Structurizr DSL. Из одной модели строятся 6 видов: System Landscape, System Context, Container, Component, Dynamic, Deployment.
- этот файл — Code-уровень C4 и 4 UML sequence-диаграммы в Mermaid. Structurizr не моделирует код, а sequence — нотация UML, а не C4.

Архитектура взята из плана задания 3, раздел 6. Имена узлов (`router`, `orchestrator`, `scanner`, `analyst`, `validator`), портов и моделей сверены с кодом `agent/graph.py`.

## Как работать с моделью

Structurizr ставится через Homebrew: `brew install structurizr`.

```bash
# проверить модель
structurizr validate -w task3-agent/diagrams/workspace.dsl

# экспорт всех видов в PNG (рендер офлайн, сервер не нужен)
structurizr export -w task3-agent/diagrams/workspace.dsl -f png -o task3-agent/diagrams/png

# UI для ручной раскладки: http://localhost:8080
structurizr local task3-agent/diagrams
```

`structurizr local` пишет в каталог `workspace.json` и `.structurizr/`. `workspace.json` хранит ручную раскладку, его можно коммитить. `.structurizr/` — индекс и логи, его коммитить не нужно.

Mermaid-диаграммы этого файла рендерятся так:

```bash
npx -p @mermaid-js/mermaid-cli mmdc -i task3-agent/diagrams/diagrams.md -o task3-agent/diagrams/mermaid/rendered.md -e png -s 2
```

Команда пишет `rendered-1.png` … `rendered-6.png` в порядке блоков. В репо они переименованы: `code-graph.png`, `code-state.png`, `seq-1-main.png` … `seq-4-gate.png`.

---

## Code

Граф LangGraph и структура его state. Сверено с `app.get_graph().draw_mermaid()`: узлы и рёбра совпадают. Подписи условных рёбер добавлены вручную: `draw_mermaid()` их не рисует.

```mermaid
flowchart TD
  start([__start__]) --> router
  router --> orchestrator
  orchestrator -.->|next_step| scanner
  orchestrator -.->|next_step| analyst
  orchestrator -.->|next_step| validator
  scanner --> orchestrator
  analyst --> orchestrator
  validator -.->|ok| finish([__end__])
  validator -.->|retry| orchestrator
```

```mermaid
classDiagram
  class State {
    messages: list
    tier: str
    kind: str
    route: dict
    evidence: list
    retries: int
    verdict: dict
  }
  class MessagesState
  MessagesState <|-- State
```

---

## Sequence 1. Основной флоу аудита кода

Запрос на SAST-проверку файла от входа до верифицированного ответа и записи в память.

```mermaid
sequenceDiagram
  autonumber
  actor U as Пользователь
  participant R as router
  participant O as orchestrator
  participant S as scanner
  participant MCP as MCP-сервер (хост)
  participant V as validator
  participant L as Ollama (хост)
  participant W as memory_writer

  U->>R: вопрос + vuln_app.py
  R->>L: systemone: kind, hard
  L-->>R: kind = scanner, hard = 0.62
  R->>O: tier = main (gemma4:e4b)
  O->>S: задача, путь vuln_app.py
  S->>L: chat (gemma4:e4b), load_skill("code-audit")
  S->>MCP: scan_code("vuln_app.py")
  MCP-->>S: total_findings=6, findings (CWE-89, CWE-78, ...)
  S-->>O: черновик отчёта + evidence
  O->>V: черновик + evidence
  V->>L: chat (qwen3:8b), JSON-схема Verdict
  L-->>V: verdict = pass
  V-->>U: верифицированный отчёт
  opt После ответа, фоном
    O->>W: история сессии
    W->>L: извлечь факты (qwen3:8b)
    W->>W: add_episode в Graphiti
  end
```

## Sequence 2. Отказ валидатора

`validator` находит неподтверждённый CWE/номер строки или число без опоры на `evidence`. `orchestrator` переделывает ответ, максимум 2 раза.

```mermaid
sequenceDiagram
  autonumber
  participant O as orchestrator
  participant V as validator
  participant L as Ollama (qwen3:8b)
  actor U as Пользователь

  O->>V: черновик «9 уязвимостей, CWE-120» + evidence «total_findings: 2, CWE-89, CWE-78»
  V->>L: chat, JSON-схема
  L-->>V: fail, unsupported_claims, fix_hint
  loop пока fail и retries <= 2
    V-->>O: fail + unsupported_claims + fix_hint
    O->>O: исправить ответ по evidence, retries += 1
    O->>V: новый черновик
    V->>L: chat, JSON-схема
    L-->>V: verdict
  end
  alt pass
    V-->>U: ответ
  else retries > 2
    V-->>U: ответ с пометкой «(Ответ не проверен валидатором.)»
  end
  Note over O,V: Langfuse: score validated и retries
```

## Sequence 3. Обновление долгой памяти

Новый факт противоречит старому. Graphiti закрывает старый факт, и в ответ попадает только действующий.

```mermaid
sequenceDiagram
  autonumber
  actor U as Пользователь
  participant O as orchestrator
  participant W as memory_writer
  participant G as Graphiti
  participant N as Neo4j

  Note over U,N: Сессия s1
  U->>O: «Отчёты аудита пиши на английском»
  O->>W: история сессии
  W->>G: add_episode
  G->>N: факт «язык = английский», valid_at = t1

  Note over U,N: Сессия s2
  U->>O: «Теперь пиши на русском»
  O->>W: история сессии
  W->>G: add_episode
  G->>G: поиск противоречий (LLM)
  G->>N: факт «язык = русский», valid_at = t2
  G->>N: старому факту invalid_at = t2

  Note over U,N: Сессия s3
  U->>O: «На каком языке писать отчёт?»
  O->>G: memory_search("язык отчётов")
  G-->>O: действующий факт: русский
  O-->>U: «На русском»
```

## Sequence 4. Гейт рискованного тула

Гибридный гейт (статические паттерны `RISKY_CODE` + decision-модель) проверяет код перед `run_python`. Рискованный код выполняется только после подтверждения пользователя. Сеть microVM блокирует выход в интернет в любом случае.

```mermaid
sequenceDiagram
  autonumber
  participant A as analyst
  participant D as gate (regex + decision-модель)
  participant G as граф LangGraph
  actor U as Пользователь
  participant P as run_python
  participant S as сеть sbx

  A->>D: проверка кода (удаление файлов, сеть, чтение вне workspace)
  D-->>A: risk = 1.0 (> 0.5)
  alt risk > 0.5
    A->>G: interrupt
    G->>U: код и запрос подтверждения
    alt пользователь подтвердил
      U-->>G: да
      G->>P: выполнить код
      P->>S: HTTP-запрос в интернет
      S-->>P: заблокировано политикой sbx
      P-->>A: вывод и ошибка сети
    else пользователь отказал
      U-->>G: нет
      G-->>A: код не выполнен
    end
  else risk ≤ 0.5
    A->>P: выполнить код
    P-->>A: вывод
  end
```
