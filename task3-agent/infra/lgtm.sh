#!/bin/bash
# Запуск grafana/otel-lgtm с дашбордом агента. Идемпотентен.
# Провижининг в образе живёт в /otel-lgtm/grafana/conf/provisioning,
# провайдер ссылается на JSON в /otel-lgtm/. Контейнер эфемерный,
# поэтому файлы досылаются docker cp после старта.
set -e
cd "$(dirname "$0")"

docker start lgtm 2>/dev/null || docker run -d --name lgtm \
  -p 3001:3000 -p 4317:4317 -p 4318:4318 grafana/otel-lgtm

sleep 8
docker cp grafana/dashboards/agent.json lgtm:/otel-lgtm/agent-dashboard.json
docker cp grafana/provider.yml lgtm:/otel-lgtm/grafana/conf/provisioning/dashboards/agent.yaml
docker restart lgtm >/dev/null
echo "Grafana: http://localhost:3001 (admin/admin), дашборд «Медиа-ассистент»"
