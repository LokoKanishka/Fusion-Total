# Lector Conversacional (Fusion-Total)

Producto dedicado a **lectura conversacional por voz**. Ingesta de libros/textos, lectura en bloques con TTS, control por voz, y conversación sobre lo leído.

## Funcionalidades

| Flujo | Descripción |
|-------|-------------|
| **Cargar libro** | Poner archivos `.txt` / `.epub` en `reader_library/` y ejecutar `biblioteca` para indexar |
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

## Uso

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
| `DIRECT_CHAT_PORT` | `8000` | Puerto del servidor HTTP |
| `DIRECT_CHAT_ALLOWED_TOOLS` | `tts` | Herramientas habilitadas |
| `DIRECT_CHAT_ALLTALK_URL` | `http://localhost:7851` | URL de AllTalk TTS |
| `LUCY_LIBRARY_DIR` | `<runtime>/reader_library` | Directorio de libros |
| `DIRECT_CHAT_MODEL` | auto-detect | Modelo Ollama para chat |

## Tests

```bash
python3 -m unittest tests.test_reader_mode tests.test_reader_library tests.test_reader_command_stress -v
```

## Recuperación

```bash
# Si necesitás volver al estado pre-cirugía:
git checkout pre-reader-surgery
```
