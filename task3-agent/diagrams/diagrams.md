# Диаграммы: медиа-ассистент

Диаграммы системы лежат в двух файлах:

- [workspace.dsl](workspace.dsl) — C4-модель в Structurizr DSL. Из одной модели строятся 6 видов: System Landscape, System Context, Container, Component, Dynamic, Deployment.
- этот файл — Code-уровень C4 и 4 UML sequence-диаграммы в Mermaid. Structurizr не моделирует код, а sequence — нотация UML, а не C4.

Архитектура взята из плана задания 3, раздел 6. После реализации сверить имена узлов, портов и моделей с кодом.

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

Граф LangGraph и структура его state. Сверено с `app.get_graph().draw_mermaid()` 2026-10-25: узлы и рёбра совпадают. Подписи условных рёбер добавлены вручную: `draw_mermaid()` их не рисует.

```mermaid
flowchart TD
  start([__start__]) --> router
  router --> orchestrator
  orchestrator -.->|next_step| media
  orchestrator -.->|next_step| analyst
  orchestrator -.->|next_step| validator
  media --> orchestrator
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
    evidence: list
    retries: int
  }
  class MessagesState
  MessagesState <|-- State
```

---

## Sequence 1. Основной флоу

Запрос с видео от входа до ответа и записи в память.

```mermaid
sequenceDiagram
  autonumber
  actor U as Пользователь
  participant R as router
  participant O as orchestrator
  participant M as media
  participant MCP as MCP-сервер (хост)
  participant V as validator
  participant L as Ollama (хост)
  participant W as memory_writer

  U->>R: вопрос + vtest.avi
  R->>L: systemone: kind, hard
  L-->>R: kind = media, hard = 0.62
  R->>O: tier = main (gemma4:e4b)
  O->>L: chat (gemma4:e4b), скилл video-analysis
  O->>M: задача, путь vtest.avi
  M->>MCP: detect_video(path, track=True)
  MCP-->>M: unique, mean_conf, fps
  M-->>O: результат тула
  O->>L: черновик ответа
  O->>V: черновик + evidence
  V->>L: chat (qwen3:8b), JSON-схема
  L-->>V: verdict = pass
  V-->>U: ответ
  opt После ответа, фоном
    O->>W: история сессии
    W->>L: извлечь факты (qwen3:8b)
    W->>W: add_episode в Graphiti
  end
```

## Sequence 2. Отказ валидатора

validator находит число без опоры на `evidence`. orchestrator переделывает ответ, максимум 2 раза.

```mermaid
sequenceDiagram
  autonumber
  participant O as orchestrator
  participant V as validator
  participant L as Ollama (qwen3:8b)
  actor U as Пользователь

  O->>V: черновик «1500» + evidence «1284.5»
  V->>L: chat, JSON-схема
  L-->>V: fail, unsupported_claims, fix_hint
  loop пока fail и retries < 2
    V-->>O: fail + fix_hint
    O->>O: исправить ответ, retries += 1
    O->>V: новый черновик
    V->>L: chat, JSON-схема
    L-->>V: verdict
  end
  alt pass
    V-->>U: ответ
  else retries = 2
    V-->>U: ответ с пометкой «не проверено»
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
  U->>O: «Отчёты пиши на английском»
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

decision-модель проверяет код перед `run_python`. Рискованный код выполняется только после подтверждения пользователя. Сеть microVM блокирует выход в интернет в любом случае.

```mermaid
sequenceDiagram
  autonumber
  participant A as analyst
  participant D as decision-модель
  participant G as граф LangGraph
  actor U as Пользователь
  participant P as run_python
  participant S as сеть sbx

  A->>D: systemone: код удаляет файлы, ходит в сеть, читает вне workspace?
  D-->>A: noul = 0.97
  alt вероятность > 0.5
    A->>G: interrupt
    G->>U: код и запрос подтверждения
    alt пользователь подтвердил
      U-->>G: да
      G->>P: выполнить код
      P->>S: HTTP-запрос в интернет
      S-->>P: заблокировано политикой
      P-->>A: вывод и ошибка сети
    else пользователь отказал
      U-->>G: нет
      G-->>A: код не выполнен
    end
  else вероятность ≤ 0.5
    A->>P: выполнить код
    P-->>A: вывод
  end
```
