workspace "Аудитор безопасности" "C4-модель мультиагентной системы AppSec/SecOps-анализа, задание 3" {

    !identifiers hierarchical

    model {
        user = person "Разработчик / ИБ-аналитик" "Даёт файл кода, зависимостей или лог и вопрос, получает верифицированный отчёт"
        dev = person "Инженер платформы" "Смотрит трейсы, дашборды, алерты"

        group "MacBook, Apple M5" {
            assistant = softwareSystem "Аудитор безопасности" "Локальный аудит уязвимостей кода (SAST), секретов, зависимостей (SCA) и форензика логов" {
                group "microVM sbx" {
                    cli = container "CLI" "Слой 1: проверка файла, запуск графа, /good и /bad" "Python"
                    agent = container "Граф агентов" "Слои 2–7: роутинг, планирование, действие, верификация, память, телеметрия" "Python, LangGraph" {
                        group "Слои 1–2. Вход и роутинг" {
                            input = component "input" "Проверяет тип и размер файла" "Узел графа"
                            router = component "router" "Получает kind и hard от decision-модели, выбирает tier" "Узел графа"
                        }
                        group "Слой 3. Планирование" {
                            orch = component "orchestrator" "Строит план, делегирует, пишет и исправляет ответ" "Узел графа"
                        }
                        group "Слой 4. Действие" {
                            scanner = component "scanner" "Ищет уязвимости, секреты и CVE через MCP-тулы" "create_agent"
                            analyst = component "analyst" "Пишет и запускает Python-код для форензики логов и CSV" "create_agent"
                            tools = component "tools.py" "run_python, load_skill, memory_search" "LangChain tools"
                        }
                        group "Слой 5. Верификация" {
                            gate = component "gate" "Проверяет код перед run_python (regex + decision-модель), вызывает interrupt" "Гибридный гейт"
                            validator = component "validator" "Сверяет ответ с evidence по JSON-схеме" "Узел графа"
                        }
                        group "Слой 6. Память" {
                            memw = component "memory_writer" "Извлекает факты после ответа" "Фоновая задача"
                            memory = component "memory.py" "Короткая и долгая память" "SqliteSaver, Graphiti"
                            proc = component "skills, persona, prompts" "Процедурная память (code-audit, secret-triage, log-forensics)" "Markdown"
                        }
                        group "Слой 7. Наблюдаемость" {
                            tel = component "telemetry.py" "Трейсы, метрики, JSON-логи" "Langfuse SDK, OpenTelemetry"
                        }
                        models = component "models.py" "Клиенты LLM и decide()" "httpx, langchain-ollama"
                    }
                    runpy = container "run_python" "Слой 4: выполняет код analyst, timeout 30 с" "subprocess"
                    sqlite = container "SqliteSaver" "Слой 6: короткая память по thread_id" "SQLite" "Database"
                    neo4j = container "Neo4j Community" "Слой 6: долгая память Graphiti" "Graph DB" "Database"
                }
                group "Хост macOS" {
                    ollama = container "Ollama" "gemma4:e4b, qwen3:8b, strands-decider; gpt-oss:20b по запросу" "llama.cpp и MLX, Metal"
                    mcp = container "MCP-сервер (sec-tools)" "AppSec-тулы: scan_code, scan_secrets, check_dependencies, read_code_slice" "MCPServer, AST + Regex"
                    ws = container "workspace/" "Проверяемые файлы кода и логи, смонтированы в microVM" "Каталог" "Folder"
                }
            }

            obs = softwareSystem "Observability" "Слой 7: трейсы, метрики, логи, алерты" {
                langfuse = container "Langfuse" "Трейсы, datasets, LLM-as-a-Judge" "Docker Compose"
                lgtm = container "grafana/otel-lgtm" "Prometheus, Loki, Tempo, Grafana" "Docker"
            }
        }

        cloud = softwareSystem "Облачный LLM API" "Open-weights модель, опционально" "External"
        telegram = softwareSystem "Telegram" "Канал алертов" "External"

        user -> assistant.cli "Вопрос и файл кода / лога" "CLI"
        dev -> obs.langfuse "Смотрит трейсы и evals" "HTTP"
        dev -> obs.lgtm "Смотрит дашборды и логи" "HTTP"

        assistant.cli -> assistant.ws "Кладёт проверяемый файл"
        assistant.cli -> assistant.agent.input "Запускает граф" "invoke"
        assistant.agent.input -> assistant.agent.router "Передаёт state"
        assistant.agent.router -> assistant.agent.models "decide()"
        assistant.agent.router -> assistant.agent.orch "tier, kind"
        assistant.agent.orch -> assistant.agent.models "chat"
        assistant.agent.orch -> assistant.agent.proc "load_skill"
        assistant.agent.orch -> assistant.agent.memory "memory_search"
        assistant.agent.orch -> assistant.agent.scanner "Задача аудита кода"
        assistant.agent.orch -> assistant.agent.analyst "Задача форензики логов"
        assistant.agent.orch -> assistant.agent.validator "Черновик и evidence"
        assistant.agent.orch -> assistant.agent.memw "После ответа, фоном"
        assistant.agent.scanner -> assistant.mcp "Вызов тула" "MCP :8765"
        assistant.agent.scanner -> assistant.agent.orch "Находки CWE/CVE"
        assistant.agent.analyst -> assistant.agent.gate "Код"
        assistant.agent.analyst -> assistant.agent.orch "Результат кода"
        assistant.agent.gate -> assistant.agent.models "systemone"
        assistant.agent.gate -> assistant.agent.tools "run_python"
        assistant.agent.tools -> assistant.runpy "Код" "subprocess"
        assistant.agent.validator -> assistant.agent.models "chat"
        assistant.agent.validator -> assistant.agent.orch "fail, fix_hint"
        assistant.agent.validator -> assistant.cli "Ответ после pass"
        assistant.agent.memw -> assistant.agent.models "chat"
        assistant.agent.memw -> assistant.agent.memory "add_episode"
        assistant.agent.memory -> assistant.sqlite "checkpoint" "SQLite"
        assistant.agent.memory -> assistant.neo4j "add_episode, search" "Bolt :7687"
        assistant.agent.models -> assistant.ollama "chat, systemone" "HTTP :11434"
        assistant.agent.models -> cloud "Сложные задачи" "HTTPS"
        assistant.agent.tel -> obs.langfuse "Трейсы" "HTTP :3000"
        assistant.agent.tel -> obs.lgtm "Метрики, логи" "OTLP :4318"
        assistant.runpy -> assistant.ws "Читает и пишет"
        assistant.mcp -> assistant.ws "Читает через safe()"
        obs.lgtm -> telegram "Алерты" "Bot API"

        deploymentEnvironment "Локально" {
            deploymentNode "MacBook" "Apple M5, 24 ГБ" "macOS 26.2" {
                deploymentNode "microVM sbx" "Своё ядро. Сеть: allowlist :11434, :8765, :3000, :4318, интернет закрыт" "Linux, Docker Sandboxes" {
                    containerInstance assistant.cli
                    containerInstance assistant.agent
                    containerInstance assistant.runpy
                    containerInstance assistant.sqlite
                    deploymentNode "Docker внутри sbx" "" "Docker Engine" {
                        containerInstance assistant.neo4j
                    }
                }
                deploymentNode "macOS" "Metal GPU" "macOS 26.2" {
                    containerInstance assistant.ollama
                    containerInstance assistant.mcp
                    containerInstance assistant.ws
                    deploymentNode "Docker Desktop" "" "Linux VM" {
                        containerInstance obs.langfuse
                        containerInstance obs.lgtm
                    }
                }
            }
            deploymentNode "Интернет" {
                softwareSystemInstance cloud
                softwareSystemInstance telegram
            }
        }
    }

    views {
        systemLandscape "landscape" "Система и окружение на машине разработчика" {
            include *
            autoLayout tb
        }

        systemContext assistant "context" "Система и её прямые связи" {
            include *
            autoLayout tb
        }

        container assistant "containers" "Процессы и хранилища. Группы показывают границу microVM" {
            include *
            autoLayout tb
        }

        component assistant.agent "components" "Компоненты графа агентов по 7 слоям" {
            include *
            autoLayout tb
        }

        dynamic assistant.agent "audit" "Сценарий «Проверь уязвимости в vuln_app.py»" {
            user -> assistant.cli "Вопрос и vuln_app.py"
            assistant.cli -> assistant.agent.input "Запуск графа"
            assistant.agent.input -> assistant.agent.router "Файл проверен"
            assistant.agent.router -> assistant.agent.models "kind, hard"
            assistant.agent.models -> assistant.ollama "systemone: strands-decider"
            assistant.agent.router -> assistant.agent.orch "tier = main (gemma4:e4b)"
            assistant.agent.orch -> assistant.agent.scanner "Задача, путь к файлу"
            assistant.agent.scanner -> assistant.mcp "scan_code('vuln_app.py')"
            assistant.agent.scanner -> assistant.agent.orch "findings, mean_conf"
            assistant.agent.orch -> assistant.agent.validator "Черновик и evidence"
            assistant.agent.validator -> assistant.agent.models "Проверка"
            assistant.agent.models -> assistant.ollama "chat: qwen3:8b, JSON-схема"
            assistant.agent.validator -> assistant.cli "Ответ после pass"
            assistant.agent.orch -> assistant.agent.memw "После ответа"
            assistant.agent.memw -> assistant.agent.memory "add_episode"
            assistant.agent.memory -> assistant.neo4j "Факт в граф"
            autoLayout lr
        }

        deployment * "Локально" "deployment" "Где работает каждый контейнер" {
            include *
            autoLayout lr 300 400
        }

        styles {
            element "Element" {
                color #ffffff
            }
            element "Person" {
                shape Person
                background #08427b
                stroke #052e56
            }
            element "Software System" {
                background #1168bd
                stroke #0b4884
            }
            element "Container" {
                background #438dd5
                stroke #2e6295
            }
            element "Component" {
                background #85bbf0
                stroke #5d82a8
                color #000000
            }
            element "Database" {
                shape Cylinder
            }
            element "Folder" {
                shape Folder
            }
            element "External" {
                background #999999
                stroke #6b6b6b
            }
            element "Group" {
                color #444444
            }
            element "Deployment Node" {
                background #ffffff
                stroke #666666
                color #222222
            }
        }
    }
}
