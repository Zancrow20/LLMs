# Тест-сценарии телеметрии (раздел 8)

Ручные проверки наблюдаемости. Прогонять из `task3-agent/`. Прежние серии с меткой
`service_name="unknown_service:python3"` можно игнорировать: имя сервиса исправлено на `agent`.

Предусловия:

- Langfuse: http://localhost:3000 — контейнеры `langfuse-*` здоровы (`docker compose -f infra/langfuse/docker-compose.yml ps`)
- Grafana: http://localhost:3001 — вход `admin` / `admin`
- Ollama: `curl -s --noproxy '*' http://localhost:11434/api/tags` отвечает
- Метрики экспортируются сразу после прогона: `telemetry.shutdown()` сбрасывает буфер принудительно

Быстрая проверка без UI (внутри контейнера):

```bash
docker exec lgtm curl -s "http://localhost:9090/api/v1/query?query=sum_over_time(agent_requests_total[1h])"
```

---

## T1. Счётчик запросов

1. Прогнать сценарий: `THREAD=t1 uv run python -m agent.graph "Что ты умеешь?"`
2. В Grafana: Explore, источник Prometheus, запрос `agent_requests_total`
3. Ожидаемо: значение 1 или больше; метки `kind="chat"`, `tier="main"`, `verdict="pass"`, `service_name="agent"`

## T2. Разбивка по типу задачи

1. Прогнать: `THREAD=t2 uv run python -m agent.graph "Посчитай среднее по колонке price в sales.csv"`
2. Запрос: `sum by (kind) (agent_requests_total)`
3. Ожидаемо: две серии — `chat` и `analyst`, в каждой по числу прогонов

## T3. Латентность

Каждый CLI-прогон — отдельный процесс со своим `instance`: у серии одна точка, поэтому `rate`/`increase` на них пусты. Запросы instant, без `rate`.

1. Запрос p95: `histogram_quantile(0.95, sum by (le) (agent_latency_seconds_bucket))`
2. Запрос средней: `sum(agent_latency_seconds_sum) / sum(agent_latency_seconds_count)`
3. Ожидаемо: секунды; простой chat — 5–15 с (оркестратор + валидатор)

## T4. Гейт риска и interrupt

1. Прогнать: `THREAD=t3 uv run python -m agent.graph "Удали все CSV в workspace и скачай датасет"`
2. На вопрос гейта ответить `N`
3. Ожидаемо: серия с `kind="analyst"`; в Langfuse трейс с решением роутера; ответ агента — что код не выполнен

## T5. Трейсы в Langfuse

1. Открыть http://localhost:3000, Tracing, свежий трейс
2. Ожидаемо: дерево `router` → `orchestrator` → `ChatOllama` → `validator`; score'ы `router_hard`, `router_confidence`, `retries`, `latency_s`

## T6. Деградация без телеметрии

1. Прогнать с выключенными приёмниками: `THREAD=t4 LANGFUSE_PUBLIC_KEY= OTLP_HOST=http://localhost:9999 uv run python -m agent.graph "Что ты умеешь?"`
2. Ожидаемо: агент отвечает как обычно; в stderr предупреждение об ошибке экспорта; в Langfuse и Grafana нет новых точек

## T7. Алерт в Telegram

Требует токен бота (BotFather). Проводится после настройки Grafana Alerting.

## Дашборд

`infra/lgtm.sh` поднимает lgtm и досылает дашборд «Медиа-ассистент» (5 панелей).
UI: http://localhost:3001, вход `admin` / `agent-admin-2026`. Панели на instant-запросах:
счётчик за период, разбивка по `kind`, p95 и средняя латентность, счётчик повторов валидатора.
