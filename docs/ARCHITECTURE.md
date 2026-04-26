# Fusion Reader v2 — Arquitectura Vigente

## Norte

Fusion Reader v2 es un lector conversacional por voz neural. La lectura sigue
siendo el centro del producto.

## Capas principales

```text
Lectura:
Documento -> Chunker -> TTSProvider -> AudioCache -> Navegador

Diálogo:
Micrófono -> STTProvider -> ConversationCore -> TTSProvider -> Navegador

Investigación externa:
pedido explícito -> provider aislado de Fusion -> respuesta integrada
```

## Lectura

Componentes principales:

- `fusion_reader_v2/reader.py`
- `fusion_reader_v2/tts.py`
- `fusion_reader_v2/service.py`

Propiedades:

- no depende del LLM;
- cachea audio por texto + voz + idioma;
- usa prefetch alrededor del cursor;
- puede preparar documento completo en background.

## Diálogo

Componentes principales:

- `fusion_reader_v2/dialogue.py`
- `fusion_reader_v2/conversation.py`
- `fusion_reader_v2/service.py`

Propiedades:

- usa snapshot del lector, no reemplaza al lector;
- STT principal en `8021`;
- fallback Whisper CLI;
- TTS neural por defecto para respuesta oral;
- `Dialogar` puede degradar `Supremo -> Pensamiento` para cuidar latencia oral.

## Modos de razonamiento

Componentes principales:

- `fusion_reader_v2/conversation.py`
- `fusion_reader_v2/service.py`

Modos disponibles:

1. **Normal**: 1 pasada, `think=false`. Respuesta directa.
2. **Pensamiento**: 1 pasada, `think=true`. Uso de fase de pensamiento nativa de Ollama.
3. **Supremo**: 3 pasadas (`borrador -> revisión -> final`). Auto-crítica interna.
4. **Contrapunto**: 3 pasadas dialécticas.
   - **Tesis**: Lucy Cunningham genera la respuesta base.
   - **Antítesis**: Un auditor crítico busca fallos y omisiones.
   - **Síntesis**: Lucy integra la tensión en una respuesta final "con cicatriz".

Regla de degradación:

- En modo `Dialogar` (voz), los modos `Supremo` y `Contrapunto` degradan automáticamente a `Pensamiento` para mantener la latencia por debajo de los 3-5 segundos.

## Notas

Componentes:

- `fusion_reader_v2/notes.py`
- endpoints de notas en `scripts/fusion_reader_v2_server.py`

Propiedades:

- por documento y bloque;
- editables, renombrables y borrables;
- accesibles por texto y por voz.

## Investigación externa

Componentes:

- `fusion_reader_v2/local_web_bridge.py`
- `fusion_reader_v2/openclaw_bridge.py`

Contrato:

- `ExternalResearchResult`
- activación solo ante pedido explícito

Proveedor vigente:

```text
default: auto
auto: SearXNG local -> OpenClaw fusion-research fallback
```

Reglas:

- `SearXNG` trae fuentes, snippets y URLs;
- no inventar fuentes;
- no presentar profundidad académica falsa cuando solo hay snippets;
- `spoken_answer` no debe leer URLs largas;
- `OpenClaw main` no se toca.

## TTS / STT

TTS:

- GPU Fusion: `7853`
- fallback CPU: `7851`
- Doctora/Antigravity: `7854` reservado, no usar

STT:

- principal: `8021`
- fallback CLI: `whisper`

## Puertos y frontera

```text
8010 Fusion Reader v2
7853 TTS Fusion
7851 TTS CPU fallback
7854 TTS Doctora/Antigravity
8021 STT faster-whisper
11434 Ollama
```

## Documento canónico de convivencia

Para OpenClaw/SearXNG/Antigravity:

- `docs/OPENCLAW_SEARXNG_COEXISTENCE.md`
