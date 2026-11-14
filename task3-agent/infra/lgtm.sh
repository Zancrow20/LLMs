#!/bin/bash
# Запуск grafana/otel-lgtm с дашбордом агента. Идемпотентен.
# Провижининг в образе живёт в /otel-lgtm/grafana/conf/provisioning,
# провайдер ссылается на JSON в /otel-lgtm/. Контейнер эфемерный,
# поэтому файлы досылаются docker cp после старта.
set -e
cd "$(dirname "$0")"

GRAFANA_USER="${GRAFANA_USER:-admin}"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-agent-admin-2026}"

docker start lgtm 2>/dev/null || docker run -d --name lgtm \
  -e "GF_SECURITY_ADMIN_USER=$GRAFANA_USER" \
  -e "GF_SECURITY_ADMIN_PASSWORD=$GRAFANA_PASSWORD" \
  -p 3001:3000 -p 4317:4317 -p 4318:4318 grafana/otel-lgtm

sleep 8
docker cp grafana/dashboards/agent.json lgtm:/otel-lgtm/agent-dashboard.json
docker cp grafana/provider.yml lgtm:/otel-lgtm/grafana/conf/provisioning/dashboards/agent.yaml
docker restart lgtm >/dev/null
echo "Grafana: http://localhost:3001 ($GRAFANA_USER/$GRAFANA_PASSWORD), дашборд «Аудитор безопасности»"
