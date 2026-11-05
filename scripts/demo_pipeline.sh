#!/usr/bin/env bash
# Publica un ciclo entrada/salida en el broker y muestra lo que guardó el stub.
# Requiere el stack en marcha: docker compose up -d --build
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Esperando a que el edge esté suscrito a MQTT..."
ready=0
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8088/ready >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "El edge no llegó a /ready. Revisa: docker compose logs edge" >&2
  exit 1
fi
curl -fsS http://127.0.0.1:8088/health
echo
echo
echo "Publicando persona en Comedor (el payload de Frigate trae sub_label y face; no deben salir)..."
docker compose exec -T mosquitto mosquitto_pub -h localhost -t frigate/events -s < fixtures/frigate_enter_dining.json
echo "Esperando debounce de entrada (2s)..."
sleep 3
curl -fsS "http://127.0.0.1:8090/events?event_type=person_entered_zone"
echo
echo
echo "Publicando salida de Comedor..."
docker compose exec -T mosquitto mosquitto_pub -h localhost -t frigate/events -s < fixtures/frigate_leave_dining.json
echo "Esperando debounce de salida (5s)..."
sleep 6
curl -fsS http://127.0.0.1:8090/events
echo
