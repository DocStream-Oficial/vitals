#!/bin/bash
# scripts/voice/stt_watchdog.sh — watchdog del server STT (roadmap coach-voz,
# Paso 1). Cada minuto (vía crontab, ver README.md) hace curl a /health; si
# no responde 200, reinicia el servicio systemd. Mismo patrón del watchdog
# que ya corre para el server XTTS en el box (:8100).
#
# Instalación (crontab -e del usuario mlmtuner):
#   * * * * * /home/mlmtuner/vitals-stt/stt_watchdog.sh >> /home/mlmtuner/vitals-stt/watchdog.log 2>&1

set -u

HEALTH_URL="http://127.0.0.1:8102/health"
SERVICE_NAME="vitals-stt"
TIMEOUT_S=5

status=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT_S" "$HEALTH_URL")

if [ "$status" != "200" ]; then
  echo "$(date -Iseconds) — /health respondió '$status' (esperaba 200) — reiniciando $SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
else
  echo "$(date -Iseconds) — /health OK"
fi
