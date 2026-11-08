#!/bin/bash
# Настройка алертов Grafana -> Telegram. Требует TELEGRAM_CHAT_ID в .env.
# Правила: (1) тестовый, срабатывает всегда — после скрина удаляется;
# (2) рабочий: p95 латентности > 60 с (таблица алертов в плане, раздел 8).
set -e
cd "$(dirname "$0")/.."

TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2)
CHAT_ID=$(grep '^TELEGRAM_CHAT_ID=' .env | cut -d= -f2)
[ -z "$CHAT_ID" ] || [ "$CHAT_ID" = "pending" ] && { echo "нет TELEGRAM_CHAT_ID в .env"; exit 1; }
AUTH="admin:agent-admin-2026"
BASE="http://localhost:3001"

# папка для правил
FOLDER_UID=$(curl -s --noproxy '*' -u "$AUTH" -X POST "$BASE/api/folders" \
  -H 'Content-Type: application/json' -d '{"title":"agent-alerts"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('uid') or 'agent-alerts')")
echo "folder: $FOLDER_UID"

# контактная точка Telegram
curl -s --noproxy '*' -u "$AUTH" -X POST "$BASE/api/v1/provisioning/contact-points" \
  -H 'Content-Type: application/json' -d "{
    \"name\": \"telegram-agent\",
    \"type\": \"telegram\",
    \"settings\": {\"bottoken\": \"$TOKEN\", \"chatid\": \"$CHAT_ID\",
                   \"message\": \"Grafana: {{ .Status }}\n{{ range .Alerts }}{{ .Annotations.summary }}\n{{ end }}\"},
    \"disableResolveMessage\": false}" >/dev/null
echo "contact point: telegram-agent"

# политика уведомлений по умолчанию -> telegram; проверяем статус: молчаливый сбой оставил
# приёмник "empty", и алерты никуда не шли (JOURNAL.md, 2026-11-08)
curl -s --noproxy '*' -u "$AUTH" -X PUT "$BASE/api/v1/provisioning/policies" \
  -H 'Content-Type: application/json' \
  -d '{"receiver": "telegram-agent", "group_by": ["grafana_folder", "alertname"]}' \
  -w "\npolicy HTTP %{http_code}\n"
echo "policy -> telegram-agent"

# правило 1: тестовое, всегда срабатывает при наличии метрик
curl -s --noproxy '*' -u "$AUTH" -X POST "$BASE/api/v1/provisioning/alert-rules" \
  -H 'Content-Type: application/json' -d "{
    \"title\": \"Тест: метрики агента присутствуют\",
    \"ruleGroup\": \"agent\",
    \"folderUID\": \"$FOLDER_UID\",
    \"condition\": \"A\",
    \"data\": [{\"refId\": \"A\", \"relativeTimeRange\": {\"from\": 21600, \"to\": 0},
      \"datasourceUid\": \"prometheus\",
      \"model\": {\"datasource\": {\"type\": \"prometheus\", \"uid\": \"prometheus\"},
        \"editorMode\": \"code\", \"expr\": \"sum(last_over_time(agent_requests_total[6h])) > 0\",
        \"instant\": true, \"intervalMs\": 1000, \"maxDataPoints\": 43200, \"refId\": \"A\"}}],
    \"noDataState\": \"OK\", \"execErrState\": \"Error\", \"for\": \"0m\",
    \"annotations\": {\"summary\": \"Тестовый алерт: канал Grafana -> Telegram работает\"},
    \"labels\": {\"severity\": \"test\"}, \"isPaused\": false}" >/dev/null
echo "rule: тестовая"

# правило 2: рабочее, p95 > 60 с
curl -s --noproxy '*' -u "$AUTH" -X POST "$BASE/api/v1/provisioning/alert-rules" \
  -H 'Content-Type: application/json' -d "{
    \"title\": \"p95 латентность выше 60 с\",
    \"ruleGroup\": \"agent\",
    \"folderUID\": \"$FOLDER_UID\",
    \"condition\": \"A\",
    \"data\": [{\"refId\": \"A\", \"relativeTimeRange\": {\"from\": 21600, \"to\": 0},
      \"datasourceUid\": \"prometheus\",
      \"model\": {\"datasource\": {\"type\": \"prometheus\", \"uid\": \"prometheus\"},
        \"editorMode\": \"code\",
        \"expr\": \"histogram_quantile(0.95, sum by (le) (last_over_time(agent_latency_seconds_bucket[6h]))) > 60\",
        \"instant\": true, \"intervalMs\": 1000, \"maxDataPoints\": 43200, \"refId\": \"A\"}}],
    \"noDataState\": \"OK\", \"execErrState\": \"Error\", \"for\": \"2m\",
    \"annotations\": {\"summary\": \"p95 латентности агента превысил 60 с\"},
    \"labels\": {\"severity\": \"warn\"}, \"isPaused\": false}" >/dev/null
echo "rule: p95 > 60s"

echo "Жду оценку правил (до 2 мин)..."
sleep 110
curl -s --noproxy '*' -u "$AUTH" "$BASE/api/alertmanager/grafana/api/v2/alerts" \
  | python3 -c "
import json,sys
for a in json.load(sys.stdin):
    print('alert:', a['labels'].get('alertname', a['labels'].get('rulename','?')), '|', a['status']['state'])"
