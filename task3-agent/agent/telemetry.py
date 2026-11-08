"""Телеметрия: трейсы в Langfuse, метрики в Grafana (OTLP -> grafana/otel-lgtm).

Ключи читаются из .env в корне task3-agent/ (файл в .gitignore).
Без ключей трейсинг выключен, агент работает как обычно.
"""
import os
from pathlib import Path

# Локальные сервисы ходят мимо HTTP_PROXY (JOURNAL.md, 2026-10-24).
# Делается до импорта SDK: httpx читает окружение при первом запросе.
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1")


def _load_env() -> None:
    """Прочитать .env: KEY=VALUE по строке, без перезаписи уже заданных переменных."""
    p = Path(__file__).resolve().parents[1] / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

# В песочнице sbx localhost — это сама microVM; сервисы доступны через host.docker.internal.
# .env смонтирован с хоста, поэтому хосты переписываются здесь, а не в .env.
if os.environ.get("SANDBOX_NAME"):
    for var in ("LANGFUSE_HOST", "OTLP_HOST"):
        value = os.environ.get(var, "")
        if value:
            os.environ[var] = value.replace("localhost", "host.docker.internal").replace(
                "127.0.0.1", "host.docker.internal")

LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
OTLP_HOST = os.environ.get("OTLP_HOST", "http://localhost:4318")
TRACING = bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))


def handler():
    """CallbackHandler для LangChain. None — если ключей нет, тогда трейсов нет."""
    if not TRACING:
        return None
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def send_scores(trace_id: str, values: dict) -> None:
    """Отправить route-значения как score'ы трейса.

    В Langfuse v4 (events_only) legacy PATCH /traces/{id} отключён, а POST /scores работает.
    Числовые значения удобны для фильтров и для метрики точности роутера (раздел 14).
    """
    if not TRACING or not trace_id:
        return
    import httpx

    auth = (os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
    for name, value in values.items():
        try:
            r = httpx.post(
                f"{LANGFUSE_HOST}/api/public/scores",
                json={"traceId": trace_id, "name": name, "value": value, "dataType": "NUMERIC"},
                auth=auth, timeout=10,
            )
            if r.status_code >= 300:
                print(f"[telemetry] score {name}: HTTP {r.status_code}")
        except Exception as e:  # score не должен ронять ответ пользователю
            print(f"[telemetry] score {name} не ушел: {e}")


# --- Метрики: OpenTelemetry -> OTLP -> Prometheus в grafana/otel-lgtm ---

_provider = None


def _meter():
    """Создать MeterProvider один раз. None — если OTLP-эндпоинта нет."""
    global _provider
    if _provider is not None:
        return _provider.get_meter("agent")
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": "agent", "service.version": "0.1.0"})
    exporter = OTLPMetricExporter(endpoint=f"{OTLP_HOST}/v1/metrics")
    _provider = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(exporter)])
    from opentelemetry import metrics as otel_metrics

    otel_metrics.set_meter_provider(_provider)
    return _provider.get_meter("agent")


def record(out: dict, latency_s: float, trace_id: str | None) -> None:
    """Записать один прогон: метрики в Grafana, метаданные в Langfuse."""
    route = out.get("route", {})
    attrs = {
        "tier": out.get("tier", "?"),
        "kind": out.get("kind", "?"),
        "verdict": out.get("verdict", {}).get("verdict", "?"),
    }
    try:
        meter = _meter()
        meter.create_counter("agent_requests").add(1, attrs)
        meter.create_histogram("agent_latency_seconds").record(latency_s, attrs)
        if out.get("retries", 0) > 0:
            meter.create_counter("agent_validator_retries").add(out["retries"], attrs)
    except Exception as e:  # метрики не должны ронять ответ пользователю
        print(f"[telemetry] метрики не ушли: {e}")
    send_scores(trace_id or "", {
        "router_hard": round(route.get("hard", {}).get("noul", 0), 3),
        "router_confidence": round(route.get("kind", {}).get("confidence", 0), 3),
        "retries": out.get("retries", 0),
        "latency_s": round(latency_s, 1),
    })


def shutdown() -> None:
    """Сбросить буферы: без этого последний экспорт метрик теряется."""
    if _provider is not None:
        _provider.shutdown()
