# Fusion Reader v2 — Operación

## Arranque recomendado

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
```

UI:

```text
http://127.0.0.1:8010/
```

## Healthchecks

```bash
curl -s http://127.0.0.1:7853/api/ready
curl -s http://127.0.0.1:8021/health
curl -s http://127.0.0.1:11434/api/tags
curl -s "http://127.0.0.1:8080/search?q=test&format=json" | head -c 300
curl -s http://127.0.0.1:8010/api/status
curl -s http://127.0.0.1:8010/api/dialogue/status
```

Los endpoints de Fusion ahora exponen un bloque `services` para leer rápido:

- `tts.ready`
- `tts.owner_valid`
- `stt.ready`
- `stt.fallback_ready`
- `chat.ready`
- `external_research.ready`
- `dialogue_reasoning.requested_mode`
- `dialogue_reasoning.applied_mode`
- `dialogue_reasoning.degraded`

## Verify

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
./scripts/verify_voice_port_isolation.sh
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

## Si 7853 no engancha

1. revisar `runtime/fusion_reader_v2/tts_owner.json`
2. verificar `owner=fusion_reader_v2`
3. verificar `curl -s http://127.0.0.1:7853/api/ready`
4. reiniciar Fusion cuando `7853` ya esté realmente listo

Regla:

- Fusion no debe caer a `7854`
- Fusion no debe reclamar `7852`

## Si Dialogar no escucha

1. hacer recarga fuerte del navegador
2. revisar `GET /api/dialogue/status`
3. revisar `curl -s http://127.0.0.1:8021/health`
4. confirmar permiso de micrófono del navegador
5. si el navegador niega micrófono, Dialogar debe mostrar el motivo y `Leer` debe seguir sano
6. si hubo barge-in extraño, detener y volver a activar `Dialogar`

## Si STT 8021 está caído

1. revisar `curl -s http://127.0.0.1:8021/health`
2. revisar `GET /api/dialogue/status` y confirmar `services.stt.ready=false`
3. relanzar `./scripts/start_fusion_reader_v2_stt.sh`
4. confirmar que `services.stt.fallback_ready` no esté ocultando una caída más seria del server principal

## Si Ollama está caído

1. revisar `curl -s http://127.0.0.1:11434/api/tags`
2. revisar `GET /api/dialogue/status` y confirmar `services.chat.ready=false`
3. si `Dialogar` devuelve texto humano de error, no tocar `Leer`
4. relanzar Ollama y reintentar una pregunta corta

## Si SearXNG está caído

1. revisar `curl -s "http://127.0.0.1:8080/search?q=test&format=json" | head`
2. revisar `GET /api/dialogue/status` y confirmar `services.external_research`
3. en `auto`, Fusion puede caer a `OpenClaw` si está habilitado
4. si ambas vías externas fallan, la respuesta debe seguir siendo humana y Dialogar no debe quedar mudo

## Investigación externa

Configuración por entorno:

```text
FUSION_READER_EXTERNAL_RESEARCH_PROVIDER=auto|searxng|openclaw
FUSION_READER_SEARXNG_URL=http://127.0.0.1:8080
FUSION_READER_SEARXNG_TIMEOUT=12
```

Regla operativa:

- `auto` prefiere `SearXNG`
- `OpenClaw` queda fallback
- no tocar Brave/global `web_search`

## Modo académico

```bash
./scripts/start_fusion_reader_v2_academic.sh
```

Perfil:

- `qwen3:14b-q8_0`
- thinking activo
- presupuesto de respuesta más alto

## Recuperación rápida

- si falla diálogo pero lectura sigue: priorizar no romper `Leer`
- si falla TTS GPU: usar fallback CPU mientras se diagnostica
- si falla investigación externa: responder humano, no exponer errores crudos
- si `Dialogar` devuelve texto pero no audio: mirar `voice_ok`, `audio_available` y `detail`
- si `reasoning_mode_requested=supreme` en voz: esperar `applied_mode=thinking` salvo override explícito
