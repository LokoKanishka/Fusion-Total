# Fusion Reader v2 — Estado de Continuidad

Fecha: 2026-05-06

Esta es la hoja corta para retomar el proyecto sin perderse. La historia larga
vive en `docs/HISTORY.md` y en los documentos históricos de diseño.

## Norte

Fusion Reader v2 es un lector conversacional por voz neural.

La ruta crítica de lectura debe quedar separada del LLM:

```text
Lectura:
Documento -> Chunker -> TTS -> Audio -> Navegador

Diálogo:
Micrófono -> STT -> ConversationCore/Ollama -> TTS -> Navegador
```

Si STT, Ollama o el diálogo fallan, `Leer` debe seguir funcionando.

## Estado actual

- Camino principal: `fusion_reader_v2/`
- Prototipo legacy: `scripts/openclaw_direct_chat.py`
- UI/API v2: `http://127.0.0.1:8010`
- TTS principal Fusion: `http://127.0.0.1:7853`
- TTS fallback CPU: `http://127.0.0.1:7851`
- TTS Doctora/Antigravity: `http://127.0.0.1:7854`
- STT principal configurado: `http://127.0.0.1:8021`
- STT efectivo actual: `whisper_cli` (fallback visible en UI cuando `8021` no responde)
- LLM local: Ollama `qwen3:14b-q8_0`
- Voz default: `female_03.wav`
- Idioma default: `es`
- Razonamiento activo: `thinking` (default)
- Modos de razonamiento: `normal`, `thinking`, `supreme`, `pensamiento_critico`
- Perfiles de Lucy: `academica` (default), `bohemia`
- Velo activo: `lucy` (default)

## Fronteras de voz

- Fusion no usa `7852`.
- Fusion no usa `7854`.
- Fusion solo confía en `7853` si existe
  `runtime/fusion_reader_v2/tts_owner.json` con `owner=fusion_reader_v2`.
- `verify_voice_port_isolation.sh` es la frontera operativa.

## Investigación externa vigente

Estado actual:

```text
provider default: auto
auto order: SearXNG local -> OpenClaw fusion-research fallback
```

Reglas:

- solo se activa bajo pedido explícito externo;
- la lectura no depende de esa vía;
- `SearXNG` local es el camino preferido;
- `OpenClaw` fallback usa `fusion-research`, nunca `main`;
- no usar Brave/global `web_search` para arreglar Fusion;
- Antigravity/Telegram usa `OpenClaw main` y no debe tocarse.

## Validación vigente

```text
tests.test_fusion_reader_v2: 181 OK
verify_voice_port_isolation.sh: OK
legacy reader safety: 35 tests OK
```

## Consolidación funcional reciente

Parches recientes confirmados:

```text
37a073f Prefer Fusion GPU TTS endpoint when ready
104bf5f Improve Fusion Reader startup logging
dd90001 Clarify free mode document status
d97dd52 Show active STT provider in UI
cdef8ab Respect literal document reading requests
Add PDF to Word conversion tool
```

Estado consolidado:

- TTS `7853` corregido y priorizado con owner validation.
- Startup logging `8010` corregido con log persistente y PID file.
- Modo libre/documento clarificado en API/UI.
- STT activo visible en UI; hoy el runtime puede operar con `whisper_cli` si `8021` está offline.
- Lectura literal vs interpretación corregida en modo documento.
- Herramienta auxiliar `PDF -> Word` disponible en la barra izquierda.
- Arquitectura de cinco ejes preservada y validada.
- Smoke de lectura literal validado.
- Smoke de conversión PDF -> DOCX validado con salida real en `~/Descargas`.

Último commit relevante:

```text
cdef8ab Respect literal document reading requests
```

## Historial corto de consolidación

- `ab98a44`: voces mitológicas.
- `2b7024b`: consolidación de estado.
- `37a073f`: corrección de selección TTS `7853` vs fallback `7851`.
- `104bf5f`: lifecycle/logging persistente de `8010`.
- `dd90001`: separación `document` vs `anchor` en modo libre/documento.
- `d97dd52`: proveedor STT activo visible en UI.
- `cdef8ab`: disciplina de lectura literal vs interpretación.
- [x] Background PDF → Word tool.
- [x] OCR fallback with Tesseract (Legacy).
- [x] Professional OCR with Docling GPU (Mandatory for scans/complex PDFs).
- [x] Isolated GPU venv for Docling (RTX 5090).
- [x] No silent CPU fallback for Docling.
- [x] Markdown to DOCX post-processing.
- [x] Progress reporting and cancellation.

## Herramienta auxiliar PDF -> Word

- vive como utilidad compacta de UI, separada del flujo de lectura;
- no carga el DOCX resultante en Fusion;
- no cambia el documento activo ni el modo libre/documento;
- acepta PDF por click o mini drag/drop sobre el control lateral;
- genera un DOCX editable y estructurado;
- guarda la salida en `~/Descargas` o `~/Downloads`;
- ofrece además una descarga HTTP efímera desde `/api/tools/pdf-to-docx/download/<id>`;
- omite imágenes por defecto;
- no usa LibreOffice en esta v1;
- no incluye OCR: si el PDF parece escaneado sin texto extraíble, devuelve error claro.

## Arranque recomendado

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
./scripts/fusion_memory_mcp_server.py (para memoria)
```

Notas:

- `start_fusion_reader_v2.sh` no levanta `8021` automáticamente.
- `open_fusion_reader.sh` sí contempla el arranque del STT dedicado.
- Si `8021` no está arriba, Fusion sigue operativo por `whisper_cli`.

## Memory MCP read-only v1

- Expone memoria markdown local en `runtime/fusion_reader_v2/memory/`.
- Transporte: `stdio`.
- Herramientas: `memory.list`, `memory.read`, `memory.search`, `memory.state`, `memory.boundaries`, `memory.next_steps`.
- Seguridad: solo lectura, validación estricta de path, restringido a archivos `.md`.

## Pendientes reales

- decidir si `8021` debe ser parte del launcher principal o si `whisper_cli` queda como camino operativo aceptado;
- exponer aún mejor en UI el provider STT efectivo durante diálogo oral y fallback real;
- probar `Dialogar` con micrófono real en más escenarios;
- ajustar fino VAD/barge-in según ruido ambiente y eco;
- afinar warmup/keep-hot de AllTalk GPU;
- mejorar OCR fino para PDFs escaneados largos;
- subir calidad del filtrado/ranking académico en la ruta `SearXNG` de Fusion.

## Fuentes vivas

- Reglas raíz: `AGENTS.md`
- Arquitectura: `docs/ARCHITECTURE.md`
- Operación: `docs/OPERATIONS.md`
- Convivencia OpenClaw/SearXNG: `docs/OPENCLAW_SEARXNG_COEXISTENCE.md`
- Personalidad: `docs/PERSONALITY.md`
- Historia: `docs/HISTORY.md`
