# Fusion Reader v2 - Estado de Continuidad

Fecha: 2026-04-21, continuidad saneada

Este archivo es la hoja corta para retomar el proyecto sin perderse. La bitacora
detallada vive en:

- `FUSION_READER_V2_BLUEPRINT.md`
- `FUSION_READER_V2_PERFORMANCE.md`
- `FUSION_READER_V2_DIALOGUE.md`
- `FUSION_READER_V2_PERSONALITY_WORKBOOK.md`
- `task.md`

## Norte del Producto

Fusion Reader v2 es un lector conversacional por voz neural. No es asistente
general, navegador, automatizador de escritorio ni orquestador de herramientas.

La ruta critica de lectura debe quedar siempre separada del LLM:

```text
Lectura:
Documento -> Chunker -> TTS -> Audio -> Navegador

Dialogo:
Microfono -> STT -> ConversationCore/Ollama -> TTS -> Navegador
```

Si STT, Ollama o el dialogo fallan, `Leer` debe seguir funcionando.

## Estado Actual

- Camino principal: `fusion_reader_v2/`.
- Prototipo viejo: `scripts/openclaw_direct_chat.py`, queda como laboratorio.
- Servidor v2: `scripts/fusion_reader_v2_server.py`.
- UI: `http://127.0.0.1:8010/`.
- TTS preferido: AllTalk/XTTS GPU propio de Fusion en `http://127.0.0.1:7853`.
- TTS fallback: AllTalk/XTTS CPU en `http://127.0.0.1:7851`.
- TTS de Doctora Lucy/Antigravity: `http://127.0.0.1:7854`; Fusion no debe
  usarlo ni reiniciarlo.
- Voz default: `female_03.wav`.
- Lectura, dialogo y notas usan AllTalk/XTTS por defecto; la voz local del
  navegador (`text_ack`) queda solo como opt-in con `FUSION_READER_FAST_*_ACK=1`.
- Idioma default: `es`.
- STT preferido para `Dialogar`: faster-whisper server en `http://127.0.0.1:8021`.
- STT fallback: `whisper` CLI; si Fusion arranca con `PATH` reducido, se usa
  `/home/linuxbrew/.linuxbrew/bin/whisper` cuando existe.
- Chat/dialogo: Ollama `qwen3:14b-q8_0` en `http://127.0.0.1:11434`.
- El modelo `qwen3:14b-q8_0` esta descargado en Ollama y queda como default
  por calidad de lectura filosofica y manejo de texto largo; `qwen3:14b` queda
  como opcion mas rapida si se necesita menor latencia.
- Chat/dialogo ahora expone tres modos persistentes de razonamiento en la UI:
  `Normal`, `Pensamiento` y `Pensamiento supremo`.
- Default actual de producto: `Pensamiento`, o sea una sola pasada con
  `think:true`.
- `Pensamiento supremo` hace tres pasadas internas
  (borrador -> revision -> respuesta final) y queda pensado para laboratorio
  profundo, no para la ruta critica de lectura.
- Excepcion de convivencia: si el guardian GPU detecta conflicto con juego y
  activa `FUSION_READER_GAME_COEXISTENCE_ACTIVE=1`, Fusion baja por defecto a
  `Normal` con menor presupuesto para no pelear por latencia.
- Modo academico/acceso directo: usa `qwen3:14b-q8_0`,
  `FUSION_READER_CHAT_THINK=1` y
  `FUSION_READER_CHAT_NUM_PREDICT=1536`.
- Entorno GPU RTX 5090: `/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311`.

Comandos de arranque recomendados:

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
# O, para lectura filosofica con modelo Q8 + thinking:
./scripts/start_fusion_reader_v2_academic.sh
```

`start_fusion_reader_v2.sh` detecta automaticamente AllTalk GPU de Fusion en
`7853`; si no esta disponible, usa el fallback CPU en `7851`.

Decision de convivencia del 2026-04-18:

```text
Fusion no autodetecta ni reclama 7852.
7852 puede pertenecer a otros agentes locales y responder Ready sin ser el TTS
aislado de Fusion.
Fusion tampoco confia ciegamente en cualquier servicio que responda Ready en
`7853`: el lanzador exige `runtime/fusion_reader_v2/tts_owner.json` con
`owner=fusion_reader_v2` antes de usarlo como GPU TTS propio.
```

Decision de convivencia reforzada del 2026-04-19:

```text
AllTalkProvider rechaza en runtime `7854` (Doctora Lucy) y `7852`
(historico/no asignado), incluso si llegan por FUSION_READER_ALLTALK_URL.
Doctora Lucy fue actualizada en boot.md, VOICE_PORTS.md, AGENTS/GEMINI,
SQLite boveda y bunker JSONL para que su fuente viva diga Lucy=7854 y
Fusion=7853.
```

## Capacidades Implementadas

- Carga de documentos desde navegador.
- Importacion TXT, MD, PDF, DOCX, ODT, RTF, HTML y formatos de oficina via
  LibreOffice cuando aplica.
- Un documento principal de lectura y multiples documentos de consulta para el
  laboratorio, sin romper la ruta critica de `/api/read`.
- PDF con `pdftotext`; si no hay texto suficiente, OCR con Tesseract `spa+eng`.
- OCR para PDFs escaneados con paginas, encabezados y columnas.
- Texto convertido guardado en `runtime/fusion_reader_v2/imported_texts/`.
- Chunks naturales para lectura hablada, default actual 420 caracteres.
- TTS por contrato propio (`TTSProvider`), no dependiente de SillyTavern.
- Cache de audio por texto + voz + idioma en `runtime/fusion_reader_v2/audio_cache/`.
- Prefetch configurable alrededor del cursor, default `FUSION_READER_PREFETCH_AHEAD=3`.
- Modo continuo: al terminar un audio, avanza y lee el siguiente bloque.
- Preparar documento: cachea todos los chunks en background.
- Metricas de voz por evento, proveedor, documento y chunk lento.
- Chat textual de laboratorio separado de `/api/read`.
- Selector persistente de razonamiento en laboratorio con `Normal`,
  `Pensamiento` y `Pensamiento supremo`.
- `Dialogar` ahora guarda trazas persistentes en
  `runtime/fusion_reader_v2/dialogue_trace.jsonl` con modo pedido, modo
  aplicado, degradacion, STT, chat y TTS por turno.
- Si el usuario deja el modo global en `Pensamiento supremo`, `Dialogar` por
  voz lo degrada a `Pensamiento` por defecto para cuidar latencia oral; el chat
  textual puede seguir usando `Supremo` real.
- La personalidad de `Normal` ya tiene una primera implementacion activa en
  `ConversationCore`: Lucy Cunningham, companera humana de lectura, intima,
  filosofica, calida, directa, problematizadora y con inspiracion en la actitud
  personal de Borges mas que en la imitacion de su escritura.
- La personalidad de `Pensamiento` ya tiene una primera implementacion activa en
  `ConversationCore`: Lucy Cunningham en version mas sobria, exigente y
  filosofico-tecnica, con prioridad por validez, genealogia conceptual,
  reconstruccion argumental, contradicciones e hipotesis de lectura.
- Modo `Dialogar`: microfono del navegador, STT, Qwen/Ollama, respuesta por voz,
  y barge-in inicial desde el navegador.
- STT persistente GPU con faster-whisper para evitar el costo de cargar Whisper
  por cada frase.
- Modo `Dialogar` estabilizado con pre-roll de microfono, silencio mas tolerante,
  captura PCM en navegador y empaquetado `audio/wav` directo para STT, prioridad
  sobre `Preparar documento` y respuestas habladas mas breves.
- Notas por documento/bloque con panel compacto, renombrado, edicion, borrado y
  creacion por voz/texto desde `Dialogar`.
- Filtro anti-alucinaciones de STT para frases espurias de Whisper como
  "suscribete" o cierres de video antes de llegar al chat, notas o UI.

## API Actual

```text
GET  /
GET  /health
GET  /api/status
GET  /api/library
GET  /api/voice/voices
GET  /api/voice/metrics
GET  /api/voice/metrics/summary
GET  /api/voice/metrics/documents
GET  /api/voice/metrics/chunks
GET  /api/import-status?id=<job_id>
GET  /api/prepare/status
GET  /api/notes
GET  /api/dialogue/status
GET  /audio/<archivo.wav>
HEAD /audio/<archivo.wav>

POST /api/load
POST /api/import
POST /api/import-file
POST /api/import-file/start
POST /api/read
POST /api/next
POST /api/previous
POST /api/jump
POST /api/prepare/start
POST /api/prepare/cancel
POST /api/notes/create
POST /api/notes/update
POST /api/notes/rename
POST /api/notes/delete
POST /api/chat
POST /api/dialogue/turn
POST /api/dialogue/reset
POST /api/reasoning/mode
POST /api/voice/test
```

Para documentos grandes, la UI debe usar `POST /api/import-file/start` y luego
consultar `GET /api/import-status?id=<job_id>` para mostrar progreso.

## Verificacion Rapida

```bash
curl -s http://127.0.0.1:7853/api/ready
curl -s http://127.0.0.1:8021/health
curl -s http://127.0.0.1:8010/api/status
curl -s http://127.0.0.1:8010/api/dialogue/status
./scripts/verify_voice_port_isolation.sh
python3 -m unittest tests.test_fusion_reader_v2 -v
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Validacion actual:

```text
voice port isolation: OK
v2: 95 tests OK
```

Ultima validacion legacy registrada en memoria:

```text
legacy reader safety: 35 tests OK
```

Estado operativo al cierre:

```text
Fusion Reader v2: http://127.0.0.1:8010
AllTalk GPU:      http://127.0.0.1:7853
STT server GPU:   http://127.0.0.1:8021
Ollama:           http://127.0.0.1:11434
Documento activo: Análisis Filosófico de _“El giro estadístico del logos”_ y su Audio Complementario
Bloque activo:    11 de 387
Preparacion:      idle; ventana de prefetch activa alrededor del cursor
```

El fallback CPU `7851` puede quedar vivo, pero Fusion debe preferir su GPU
aislada solo en `7853`.

Nota de reparacion 2026-04-20: si la UI muestra `TTS no disponible` con
`tts_owner_pid_stale`, verificar `runtime/fusion_reader_v2/tts_owner.json` contra
el PID real de `7853`. La reparacion limpia es reiniciar AllTalk GPU con
`./scripts/start_reader_neural_tts_gpu_5090.sh`, no desactivar el chequeo de
dueno. En esta pasada se recupero `7853` con owner valido, STT `8021`, Fusion
`8010`, y se restauro el documento activo anterior.

Nota de red local 2026-04-20: esta maquina tenia cable `eno1` y Wi-Fi `wlp9s0`
activos a la vez en la misma LAN `192.168.0.0/24`, con conflicto ARP/IP y rutas
alternando. Se dejo `Perfil 1` por cable como ruta principal (`metric 50`) y el
Wi-Fi `Fibertel Wifi125 2.4 Ghz` como respaldo (`metric 800`). Para que el usuario
no dependa de comandos, se creo un guardian local:
`/home/lucy-ubuntu/.local/bin/fusion-network-guardian.sh`, con autostart en
`/home/lucy-ubuntu/.config/autostart/fusion-network-guardian.desktop`. El guardian
mantiene Wi-Fi apagado mientras el cable tiene internet y lo prende/conecta como
backup si el cable falla.

Nota de modelo 2026-04-20: `qwen3:14b-q8_0` esta descargado en Ollama y queda
como modelo default de Fusion por calidad en lectura filosofica y manejo de texto.
`scripts/start_fusion_reader_v2.sh` exporta ese modelo si no hay override,
`fusion_reader_v2/conversation.py` lo usa como fallback interno, y el acceso
directo `/home/lucy-ubuntu/Escritorio/fusion.desktop` pasa por
`/home/lucy-ubuntu/.local/bin/fusion-reader-launcher`, que abre Fusion en modo
academico con `FUSION_READER_CHAT_THINK=1`, `NUM_PREDICT=1536` y temperatura
`0.35`. Validacion de esta pasada: modelo cargado en Ollama `100% GPU`, contexto
`32768`, keep_alive aproximado 30 minutos.

Nota de laboratorio 2026-04-20: el chat textual y `Dialogar` por voz ya no
quedan ciegos al material pegado en el propio chat cuando no hay documento
cargado o cuando el usuario pregunta "ves lo que acabo de poner". `FusionReaderV2.chat`
conserva un historial reciente separado de la ruta de lectura y `dialogue_turn_text`
lo inyecta en el snapshot oral; `ConversationCore` lo entrega como
`MATERIAL RECIENTE DEL LABORATORIO`. El documento activo sigue siendo una fuente,
pero no la unica fuente del laboratorio. Validacion: prueba real HTTP con
`qwen3:14b-q8_0` reconocio texto pegado sin documento, y
`tests.test_fusion_reader_v2` quedo en 60 OK.

Nota de continuidad de respuestas 2026-04-20: se detecto que el laboratorio
escrito dejaba frases/listas inconclusas con `qwen3:14b-q8_0` porque el perfil
academico usaba `FUSION_READER_CHAT_NUM_PREDICT=512` con thinking activado. Se
subio el perfil academico/acceso directo a `1536`, el fallback interno con
thinking a `1024`, y el prompt ahora pide cerrar pocas ideas completas antes de
abrir listas largas. El recorte de voz de `Dialogar` subio a 520 caracteres y
ya no termina con `...` cuando corta por longitud. Validacion:
`tests.test_fusion_reader_v2` quedo en 63 OK.

Nota de limpieza de laboratorio 2026-04-20: la UI del panel Laboratorio ahora
tiene boton `Borrar historial`. Llama `POST /api/laboratory/reset` (alias
`/api/chat/reset`), limpia el panel visible y borra tanto `_chat_history` como
`_dialogue_history`, de modo que el texto pegado y los turnos de `Dialogar` dejan
de condicionar respuestas nuevas. Validacion: `tests.test_fusion_reader_v2`
quedo en 65 OK.

Nota de estabilidad GPU 2026-04-20: tras un cuelgue duro de la maquina, el boot
anterior mostro 96 eventos `NVRM` del driver NVIDIA entre 18:52 y 19:18,
despues de lanzar Baldur's Gate 3/Steam mientras la maquina tambien tenia cargas
CUDA de lectura/LLM. No hubo evidencias de OOM, disco, filesystem ni temperatura
en los logs revisados. La medicion posterior con BG3 activo mostro carga media,
no saturacion: ~5.2 GiB VRAM para BG3, ~6.5 GiB VRAM total, ~23-26% GPU,
~123-125 W y ~4.7 GiB RAM del proceso. Por eso la prevencion no debe ser
"cerrar el juego". Se agrego `scripts/fusion_reader_gpu_guard.sh` como detector
de convivencia y se ajustaron los lanzadores para modo convivencia:
`open_fusion_reader.sh` usa STT CPU/int8 y AllTalk CPU fallback cuando detecta
BG3/Steam app `1086940`; `start_fusion_reader_v2.sh` mantiene Fusion abierto
pero reduce chat a `think=0`, `num_ctx=8192` y `num_predict=384`, y prefiere TTS
CPU fallback. La politica default es advertir, no bloquear. Solo bloquea si se
exporta `FUSION_READER_GPU_CONFLICT_POLICY=block`; se puede forzar GPU completa
con `FUSION_READER_ALLOW_GPU_WITH_GAMES=1` o desactivar el modo con
`FUSION_READER_GAME_COEXISTENCE=0`.

Nota de captura STT 2026-04-21: `Dialogar` ya no depende de `MediaRecorder`
ni de contenedores WebM para la captura principal. La UI arma PCM en el
navegador y lo sube como `audio/wav`, porque los WebM intermitentes estaban
rompiendo transcripciones con `EBML header parsing failed`. Si el usuario sigue
viendo que Fusion "no escucha", primero hacer recarga fuerte de la pestana
(`Ctrl+Shift+R` en Chromium) y luego revisar los logs del STT buscando
`convert_failed` o `empty_transcript`.

## Archivos de Referencia

- `AGENTS.md`: reglas raiz del agente.
- `FUSION_READER_V2_BLUEPRINT.md`: arquitectura y contratos.
- `FUSION_READER_V2_PERFORMANCE.md`: GPU TTS, prefetch y preparar documento.
- `FUSION_READER_V2_DIALOGUE.md`: diseno e implementacion de `Dialogar`.
- `README.md`: uso general.
- `task.md`: tablero corto de misiones y pendientes.

## Pendientes Reales

- Diseñar personalidad profunda de Fusion por modo (`Normal`, `Pensamiento`,
  `Supremo`) sin volverlo asistente general.
- Completar `FUSION_READER_V2_PERSONALITY_WORKBOOK.md` con decisiones del
  usuario y luego cablear esa personalidad en `ConversationCore`.
- Probar en navegador la nueva dinamica `principal + consulta` y afinar la UI
  de promocion/quitado si hace falta menos friccion.
- Seguir probando `Dialogar` con microfono real, especialmente interrupcion
  natural mientras Fusion habla.
- Ajustar VAD/barge-in segun ruido ambiente y eco si vuelve a cortar tarde,
  temprano o si captura audio de la respuesta.
- Afinar warmup/keep-hot de AllTalk GPU para reducir primera sintesis lenta.
- Agregar pausa/reanudar como estado real de lectura.
- Seguir mejorando OCR fino en PDFs escaneados largos.
- Cuando el usuario lo pida, preparar una limpieza de commit separando v2,
  docs, scripts GPU/STT y cambios legacy.
