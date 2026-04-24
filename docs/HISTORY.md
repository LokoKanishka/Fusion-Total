# Fusion Reader v2 — Historia

## Línea corta

### Prototipo legacy

- `scripts/openclaw_direct_chat.py` quedó como laboratorio y compatibilidad.

### Nacimiento de v2

- se abre `fusion_reader_v2/` como reescritura voice-first
- se separa lectura de conversación

### GPU RTX 5090

- se valida entorno GPU aislado
- `7852` queda solo como medición histórica
- `7853` pasa a ser el puerto reservado de Fusion

### Frontera de voz

- Doctora/Antigravity reserva `7854`
- Fusion exige `tts_owner.json` para confiar en `7853`
- nace `verify_voice_port_isolation.sh`

### Dialogar

- se implementa modo oral con STT persistente y TTS neural
- se suma barge-in inicial

### Personalidad

- se define Lucy Cunningham
- aparecen `Normal`, `Pensamiento` y `Supremo`
- `Modo libre` desacopla conversación y texto cuando hace falta

### OpenClaw externo

- Fusion integra `fusion-research`
- se humanizan errores de cuota/rate limit

### SearXNG aislado

- Fusion deja de depender del web layer global de OpenClaw para búsqueda normal
- `SearXNG` local pasa a ser la primera vía de investigación externa
- `OpenClaw fusion-research` queda fallback

## Fuente viva

Este archivo es histórico. El estado operativo vigente está en:

- `FUSION_READER_V2_STATE.md`
- `docs/ARCHITECTURE.md`
- `docs/OPERATIONS.md`
- `docs/OPENCLAW_SEARXNG_COEXISTENCE.md`
