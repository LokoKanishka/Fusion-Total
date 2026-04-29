# Fusion Reader v2 — Estado de Continuidad

Fecha: 2026-04-28

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
- STT principal: `http://127.0.0.1:8021`
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
tests.test_fusion_reader_v2: 160 OK
verify_voice_port_isolation.sh: OK
legacy reader safety: 35 tests OK

- Velo conversacional v1.1 implementado. Paleta narrativa afilada.
- Disciplina de cierre conversacional implementada (menos preguntas artificiales).
- Modo Libre (Free Mode): corregido para no inyectar documento por defecto (independencia real).
- Botón "Limpiar documento" agregado a la UI y API.
- Personalidades refinadas: Lucy Académica (rigurosa) y Lucy Bohemia (libre).
- Bohemia uncensored: agregada rienda narrativa contra loops y teatralidad excesiva.
- Selector de voz TTS dinámico: implementado en UI y API con persistencia.
- Etiquetas de voz amigables: implementadas para el catálogo AllTalk, agrupadas por categorías (Femeninas, Masculinas, Especiales).
- modelo: huihui_ai/qwen3-abliterated:14b-v2-q8_0
- smoke test: OK
- tests: 163 OK (más labels visuales validados por grep)
- voice isolation: OK
- arquitectura de cinco ejes (Anclaje, Perfil, Razonamiento, Velo, Voz) preservada y validada.
```

Último commit relevante:

```text
579eed4 Make free mode fully detach from document context
```

## Arranque recomendado

```bash
./scripts/start_reader_neural_tts_gpu_5090.sh
./scripts/start_fusion_reader_v2_stt.sh
./scripts/start_fusion_reader_v2.sh
```

## Pendientes reales

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
