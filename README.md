# Lector Conversacional (Fusion-Total)

Producto dedicado a **lectura conversacional por voz**. Ingesta de libros/textos, lectura en bloques con TTS, control por voz, y conversación sobre lo leído.

## Estado Actual

El proyecto tiene dos caminos:

- **Prototipo estable** en `scripts/openclaw_direct_chat.py`, puerto `8000`.
- **Fusion Reader v2** en `fusion_reader_v2/`, puerto `8010`, reescritura voice-first alrededor de AllTalk/XTTS.

La direccion principal nueva es la v2. El prototipo viejo queda como laboratorio y referencia hasta que la v2 lo supere.

Documentos de continuidad:

- `AGENTS.md`
- `FUSION_READER_V2_BLUEPRINT.md`
- `FUSION_READER_V2_STATE.md`
- `FUSION_READER_V2_PERFORMANCE.md`
- `FUSION_READER_V2_DIALOGUE.md`

## Funcionalidades

| Flujo | Descripción |
|-------|-------------|
| **Cargar libro** | Subir `.txt`, `.md` o `.pdf` desde la UI, o ponerlos en `library/` y ejecutar `biblioteca`/rescan |
| **Leer por voz** | `leer libro <n>` inicia lectura TTS bloque a bloque |
| **Detener** | `para` / `detener` / barge-in por voz |
| **Navegar** | `siguiente`, `repetir`, `ir al párrafo 12`, `continuar desde "frase"`, `volver una frase` |
| **Conversar** | Cualquier mensaje no-comando se procesa como chat sobre el bloque actual |
| **Retomar** | Bookmark automático; `continuar desde "frase"` para retomar |

## Arquitectura

```
scripts/openclaw_direct_chat.py     ← monolito: HTTP server + reader engine + voice/TTS/STT
scripts/molbot_direct_chat/
  ├── reader_ui_html.py              ← UI HTML del lector
  ├── stt_local.py                   ← STT local (whisper)
  └── util.py                        ← utilidades compartidas
```

Arquitectura v2:

```
fusion_reader_v2/
  ├── reader.py                      ← Document, chunking, session/cursor
  ├── tts.py                         ← TTSProvider, AllTalkProvider, AudioCache
  ├── dialogue.py                    ← STTProvider, faster-whisper/Whisper fallback
  ├── conversation.py                ← chat acotado al documento activo
  ├── notes.py                       ← notas por documento/bloque
  ├── documents.py                   ← importacion y OCR
  ├── metrics.py                     ← metricas de voz/cache
  └── service.py                     ← FusionReaderV2: lectura, cache, prefetch, notas, dialogo
scripts/fusion_reader_v2_server.py   ← servidor HTTP + UI web v2
```

## Uso

### Fusion Reader v2

```bash
# Terminal 1: levantar voz neural GPU en RTX 5090
./scripts/start_reader_neural_tts_gpu_5090.sh

# Terminal 2: levantar STT persistente GPU para Dialogar
./scripts/start_fusion_reader_v2_stt.sh

# Terminal 3: levantar lector v2
./scripts/start_fusion_reader_v2.sh

# Abrir
http://127.0.0.1:8010/
```

La v2 reserva AllTalk/XTTS GPU propio en `127.0.0.1:7853`.
Si no responde, cae al fallback CPU en `127.0.0.1:7851`. La voz default es
`female_03.wav`, idioma `es`.

Doctora Lucy/Antigravity reserva su TTS en `127.0.0.1:7854`. Fusion no lo usa,
no lo mata y no lo toma como fallback.

`127.0.0.1:7852` no se autodetecta como proveedor de Fusion: puede pertenecer a
otros agentes locales y no debe ser reclamado por el lector.

Ademas, Fusion solo confia en el GPU TTS de `7853` si el archivo
`runtime/fusion_reader_v2/tts_owner.json` confirma `owner=fusion_reader_v2`.
Esto evita que otro AllTalk vivo con `Ready` sea usado por accidente.

En la RTX 5090, el entorno GPU aislado esta en:

```text
/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311
```

El lector prepara una ventana de audio alrededor del cursor para reducir la
espera al saltar de bloque:

```bash
FUSION_READER_PREFETCH_AHEAD=3
```

Para dejar un documento casi instantaneo durante la lectura, usá
`Preparar documento` en la UI. Eso cachea todos los bloques en background y se
puede cancelar desde el mismo panel.

El laboratorio tiene modo `Dialogar`: usa el microfono del navegador, captura
PCM y construye `audio/wav` directo en la UI, transcribe con un STT persistente
GPU (`faster-whisper` en `127.0.0.1:8021`), conversa con Ollama/Qwen sobre el
documento y responde con voz neural. Tambien permite cortar la respuesta
hablando encima desde el navegador. Si el servidor STT GPU no esta disponible,
Fusion cae al fallback `whisper` CLI, que es mas lento porque carga el modelo
por turno.

Si `Dialogar` parece no escuchar despues de una actualizacion del front, hacé
recarga fuerte de la pestana (`Ctrl+Shift+R` en Chromium) antes de diagnosticar.

Las notas del documento se guardan por documento y bloque. El panel lateral
lista entradas compactas como `B45 idea breve`, permite renombrarlas, editarlas
o borrarlas, y el modo `Dialogar` puede crear notas por voz sin pasar por el LLM.

La zona de carga de la v2 acepta documentos desde el navegador y los convierte a texto para leer:

- TXT / MD / texto plano
- PDF mediante `pdftotext`
- PDF escaneado mediante OCR con Tesseract (`spa+eng`)
- DOCX / ODT mediante extraccion interna
- RTF / HTML con limpieza basica
- otros formatos de oficina mediante LibreOffice headless cuando sea posible

Los documentos convertidos quedan como texto liviano en:

```text
runtime/fusion_reader_v2/imported_texts/
```

Estado rapido:

```bash
curl -s http://127.0.0.1:7853/api/ready
curl -s http://127.0.0.1:8021/health
curl -s http://127.0.0.1:8010/api/status
curl -s http://127.0.0.1:8010/api/prepare/status
curl -s http://127.0.0.1:8010/api/dialogue/status
```

### Prototipo viejo

```bash
# Configurar variables de entorno
export OLLAMA_HOST=http://localhost:11434
export DIRECT_CHAT_ALLOWED_TOOLS=tts

# Iniciar servidor
python3 scripts/openclaw_direct_chat.py

# Acceder al lector en http://localhost:8000
```

## Variables de Entorno Clave

| Variable | Default | Descripción |
|----------|---------|-------------|
| `DIRECT_CHAT_HTTP_PORT` | `8000` | Puerto del servidor HTTP |
| `DIRECT_CHAT_ALLOWED_TOOLS` | `tts` | Herramientas habilitadas |
| `DIRECT_CHAT_ALLTALK_URL` | `http://localhost:7851` | URL de AllTalk/XTTS para voz neural |
| `DIRECT_CHAT_ALLTALK_VOICE` | `female_01.wav` | Voz AllTalk/XTTS del prototipo viejo |
| `LUCY_LIBRARY_DIR` | `<runtime>/library` | Directorio de libros |
| `DIRECT_CHAT_MODEL` | auto-detect | Modelo Ollama para chat |
| `FUSION_READER_ALLTALK_URL` | auto `7853` o fallback `7851` | URL TTS usada por Fusion Reader v2 |
| `FUSION_READER_VOICE` | `female_03.wav` | Voz AllTalk/XTTS usada por lectura y comentarios de Fusion v2 |
| `FUSION_READER_GPU_TTS_PORT` | `7853` | Puerto GPU reservado para Fusion Reader v2 |
| `FUSION_READER_PREFETCH_AHEAD` | `3` | Cantidad de bloques futuros que se preparan alrededor del cursor |
| `FUSION_READER_STT_URL` | `http://127.0.0.1:8021` | URL del servidor STT persistente para `Dialogar` |
| `FUSION_READER_STT_MODEL` | `small` | Modelo Whisper/faster-whisper para STT |
| `FUSION_READER_STT_PROVIDER` | `auto` | Usa STT server si esta vivo; fallback a Whisper CLI |
| `FUSION_READER_CHAT_MODEL` | `qwen3:14b-q8_0` | Modelo Ollama default para dialogo sobre el texto |
| `FUSION_READER_FAST_DIALOGUE_ACK` | `0` | Opt-in: responde dialogo con voz local rapida del navegador; por defecto usa AllTalk/XTTS `female_03.wav` |
| `FUSION_READER_FAST_NOTE_ACK` | `0` | Opt-in: confirma notas con voz local rapida del navegador; por defecto usa AllTalk/XTTS `female_03.wav` |
| `FUSION_READER_CHAT_THINK` | `0` | Desactiva thinking de Qwen/Ollama para dialogo fluido; usar `1` solo para analisis largo |
| `FUSION_READER_CHAT_NUM_PREDICT` | `384` (`1536` con perfil academico) | Tokens maximos de respuesta para evitar respuestas cortadas en modo pensamiento |

Modo academico local:

```bash
./scripts/start_fusion_reader_v2_academic.sh
```

El acceso directo del escritorio abre este perfil academico. Usa `qwen3:14b-q8_0`, activa thinking y sube el margen de respuesta para analisis de textos sin cortar frases.

## Tests

```bash
./scripts/verify_voice_port_isolation.sh
python3 -m unittest tests.test_fusion_reader_v2 -v
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

Ultima validacion conocida:

- aislamiento puertos voz: OK
- v2: 76 tests OK
- v2 diálogo: incluido en la suite actual
- lector legacy: 35 tests OK
