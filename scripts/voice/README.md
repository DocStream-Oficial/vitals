# Server STT del Coach de Vitals (notas de voz)

Server FastAPI standalone (`stt_server.py`) que transcribe audio con
`openai-whisper`. Vive en el WSL del box (DocStreamGaming), **NO** en el
proceso de Vitals — nada en `app/` lo importa. Expuesto a Windows en
`http://127.0.0.1:8102` vía wslrelay (mismo mecanismo que ya expone el
server XTTS en `:8100`).

Contrato:
- `GET /health` → `{"ok": true, "model_loaded": bool, "model": "<nombre>"}`
- `POST /transcribe?lang=es` — body: audio crudo (raw bytes), header
  `Content-Type` = `audio/mp4` o `audio/webm` (lo que grabe el navegador) →
  `{"text": "...", "duration_s": 12.3}`. Body vacío → 400; >15 MB → 413;
  modelo no disponible o whisper/ffmpeg fallan → 503 `{"error": "..."}`.

## Instalación en el box (WSL, usuario `mlmtuner`)

1. Copiar estos archivos al box (SFTP o `scp`):
   ```
   scripts/voice/stt_server.py       -> ~/vitals-stt/stt_server.py
   scripts/voice/vitals-stt.service  -> ~/vitals-stt/vitals-stt.service
   scripts/voice/stt_watchdog.sh     -> ~/vitals-stt/stt_watchdog.sh
   ```
2. Verificar que el venv `~/CosyVoice/venv` tiene `openai-whisper` + `torch`
   con CUDA (ya probado en vivo — ver roadmap `_dev-harness/coach-voz/ROADMAP.md`,
   sección "Contexto de infraestructura"). Si falta algo:
   ```
   ~/CosyVoice/venv/bin/pip install -U openai-whisper
   ```
3. Instalar y habilitar el servicio systemd:
   ```
   sudo cp ~/vitals-stt/vitals-stt.service /etc/systemd/system/vitals-stt.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now vitals-stt
   ```
   La primera corrida descarga el modelo whisper `medium` (~1.5 GB) —
   esperar a que `GET /health` reporte `model_loaded: true` antes de probar
   `/transcribe` (puede tardar unos minutos según la conexión).
4. Dar permisos de ejecución al watchdog e instalarlo en crontab:
   ```
   chmod +x ~/vitals-stt/stt_watchdog.sh
   crontab -e
   # agregar:
   * * * * * /home/mlmtuner/vitals-stt/stt_watchdog.sh >> /home/mlmtuner/vitals-stt/watchdog.log 2>&1
   ```
   El watchdog necesita `sudo systemctl restart vitals-stt` sin password —
   si `mlmtuner` no tiene ya ese permiso (revisar cómo está configurado para
   el watchdog de XTTS, debería ser el mismo criterio), agregar una regla en
   `/etc/sudoers.d/` acotada a ese comando exacto.

## Probar

Desde Windows (PowerShell), tras el paso 3:
```
curl http://127.0.0.1:8102/health
curl -X POST "http://127.0.0.1:8102/transcribe?lang=es" -H "Content-Type: audio/wav" --data-binary "@prueba.wav"
```

## Puertos en uso en el box (referencia)

- `:8100` — XTTS-v2 (Alfred / OpenClaw, compartido con el Coach de Vitals
  para TTS — NO tocar su config ni su `tempo`).
- `:8101` — CosyVoice3 (ajeno, no tocar).
- `:8102` — este server (STT del Coach de Vitals).

## Override del modelo

`WHISPER_MODEL` (env var, en el `.service` o el shell antes de arrancar
manual) — default `medium`. Modelos más chicos (`small`, `base`) son más
rápidos pero transcriben peor español coloquial; no bajar de `small` sin
probar calidad real primero.

## Rollback

El server STT puede quedarse corriendo si se revierte el deploy de Vitals —
nadie más lo consume y no tiene efectos colaterales fuera de sí mismo:
```
sudo systemctl stop vitals-stt
sudo systemctl disable vitals-stt
```
