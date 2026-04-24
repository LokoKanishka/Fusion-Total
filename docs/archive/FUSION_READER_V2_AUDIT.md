# Fusion Reader v2 - Auditoria de Orden

Nota 2026-04-24:

```text
Archivo archivado. Conserva una auditoría anterior de orden del repo y ya no es
fuente viva de estado operativo.
```

Fecha: 2026-04-19

Nota 2026-04-21:

```text
Este archivo queda como auditoria historica de orden.
La validacion canonicamente vigente hoy es: voice port isolation OK,
tests.test_fusion_reader_v2 = 76 OK, legacy reader safety = 35 OK.
```

## Resumen

Fusion Reader v2 esta operativo y separado del prototipo viejo, pero el repo
sigue en transicion. La ruta viva es:

```text
fusion_reader_v2/
scripts/fusion_reader_v2_server.py
scripts/fusion_reader_v2_stt_server.py
scripts/start_fusion_reader_v2*.sh
scripts/start_reader_neural_tts*.sh
tests/test_fusion_reader_v2.py
```

El prototipo legacy (`scripts/openclaw_direct_chat.py`, `app/`,
`scripts/molbot_direct_chat/`) queda como laboratorio y compatibilidad. No debe
borrarse sin una migracion explicita.

## Hallazgos

- Documentacion y bootstrap aun apuntaban a `7852` como GPU preferido. Eso era
  peligroso porque `7852` puede pertenecer a otro agente local. Se actualizo la
  ruta operativa a `7853` y se dejaron las referencias `7852` solo como historia
  o prueba de no-autodeteccion.
- `package.json` era un resto generico (`infra`) con `npm test` fallando a
  proposito. Se convirtio en un envoltorio util para test v2 y chequeo JS.
- `.codex` y `.pytest_cache/` no estaban ignorados de forma explicita. Se
  agregaron al `.gitignore`.
- La documentacion principal tenia conteos viejos de validacion.
  En 2026-04-21 la continuidad canonicamente saneada quedo en 76 tests v2.
- El listado de endpoints estaba incompleto: faltaban notas y `/api/chat` en
  algunos documentos.
- Hay artefactos locales ignorados grandes (`runtime/`, `data/`, `output/`) y
  caches Python/Playwright. No forman parte del codigo activo.

## Riesgos Que No Conviene Tocar A Ciegas

- `data/` y `runtime/` pesan mucho, pero pueden contener cache de audio,
  documentos convertidos y memoria local. Limpiarlos requiere una decision
  explicita de retencion.
- `app/` y `scripts/openclaw_direct_chat.py` son legacy, pero los tests de
  seguridad del lector todavia dependen de ellos.
- `scripts/lucy_sensor_client.py`, `scripts/x11_file_agent.py` y configs n8n
  son ajenos al norte del producto. No deben meterse como features de Fusion,
  pero pueden pertenecer al entorno local de Lucy.

## Contrato Operativo Actual

```text
Fusion Reader v2 UI/API: http://127.0.0.1:8010
STT faster-whisper:      http://127.0.0.1:8021
TTS GPU Fusion:          http://127.0.0.1:7853
TTS fallback CPU:        http://127.0.0.1:7851
Puerto no reclamado:     7852
```

## Corte Limpio Recomendado

Trackear como producto vivo:

- `fusion_reader_v2/`
- `scripts/fusion_reader_v2_server.py`
- `scripts/fusion_reader_v2_stt_server.py`
- `scripts/start_fusion_reader_v2*.sh`
- `scripts/start_reader_neural_tts*.sh`
- `scripts/open_fusion_reader.sh`
- `scripts/fusion_reader_gpu_guard.sh`
- `scripts/verify_voice_port_isolation.sh`
- `scripts/bootstrap_alltalk_gpu_5090.sh`
- `scripts/check_gpu_5090_env.py`
- `scripts/gpu_compat/`
- `tests/test_fusion_reader_v2.py`
- `FUSION_READER_V2_*.md`
- `AGENTS.md`
- `README.md`
- `task.md`
- `agente/`
- `assets/icons/fusion_red.svg`

Mantener como legacy necesario mientras v2 convive:

- `app/`
- `scripts/openclaw_direct_chat.py`
- `scripts/molbot_direct_chat/`
- `tests/test_reader_mode.py`
- `tests/test_reader_library.py`
- `tests/test_reader_command_stress.py`

Ignorar como estado local o artefacto de laboratorio:

- `runtime/`
- `data/`
- `output/`
- `library/notes/`
- `library/uploads/`
- `LUCY_REPORT.md`
- `LUCY_TASK.md`

Fixtures de biblioteca que si conviene conservar visibles:

- `library/1cunn.txt`
- `library/diego_audio.txt`
- `library/largo_test.txt`
- `library/seek_test.txt`

## Validacion Esperada

```bash
python3 -m unittest tests.test_fusion_reader_v2 -v
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
npm run check:ui
```

## Push Readiness

Estado al 2026-04-21:

- `origin` existe y apunta a `https://github.com/LokoKanishka/Fusion-Total.git`.
- No se detectaron secretos hardcodeados obvios en la pasada rapida de codigo y docs.
- Los artefactos locales mas claros quedan fuera por `.gitignore`: `runtime/`,
  `data/`, `output/`, `workspace/`, `state/`, `library/notes/`,
  `library/uploads/`, `LUCY_REPORT.md`, `LUCY_TASK.md`, logs y `cmd_pipe`.
- La suite v2 esta verde (`76 OK`).

Riesgos antes de pushear bruto:

- El repo todavia mezcla una migracion grande: v2 nueva, legacy modificado,
  docs de continuidad y scripts operativos, todo sin separar en commits.
- `config/project_isolation.env` contiene rutas/containers locales; no parece
  secreto, pero si se publica conviene asumir que es configuracion de entorno,
  no contrato portable.
- Hay archivos nuevos reales del producto que aun no fueron integrados a la
  historia git; eso no bloquea el push, pero si hace que el snapshot quede
  dificil de revisar.

Recomendacion media para push:

1. Empujar primero el nucleo `fusion_reader_v2/`, sus scripts, tests y docs.
2. Incluir legacy solo si hace falta para mantener la suite `35 OK`.
3. Evitar subir artefactos locales o reportes de laboratorio.
4. Separar en commits por area antes de abrir PR o publicar el repo.
