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
4. **Pensamiento crítico** (anteriormente Contrapunto): 3 pasadas dialécticas.
   - **Tesis**: Lucy Cunningham genera la respuesta base.
   - **Antítesis**: Un auditor crítico busca fallos y omisiones.
   - **Síntesis**: Lucy integra la tensión en una respuesta final "con cicatriz".

Regla de degradación:

- En modo `Dialogar` (voz), los modos `Supremo` y `Pensamiento crítico` degradan automáticamente a `Pensamiento` para mantener la latencia por debajo de los 3-5 segundos.

## Eje de Anclaje

Fusion Reader v2 permite alternar entre dos modos de anclaje (Laboratory Mode):

1. **Modo Documento** (default):
   - Lucy responde anclada al documento activo y al bloque visible.
   - El contexto incluye el fragmento que el usuario está viendo.
2. **Modo Libre**:
   - Lucy conversa libremente, sin estar anclada al documento activo.
   - **Independencia Documental:** El documento activo NO se inyecta en el contexto por defecto. Esto evita que Lucy interprete todo a través del fragmento visible.
   - **Acceso Explícito:** El documento se incluye en el prompt SOLO si el usuario lo pide explícitamente (ej: "qué dice el texto", "según el fragmento", "mirá el documento").
   - El perfil (Académica/Bohemia) y el Velo siguen activos, tiñendo el tono de la conversación libre.

## Perfiles de personalidad

Fusion Reader v2 permite alternar entre dos perfiles de Lucy:

1. **Académica** (default):
   - personalidad sobria, exigente y filosófico-técnica;
   - inspirada en una presencia borgiana, contemplativa y precisa;
   - usa `FUSION_READER_CHAT_MODEL` (default `qwen3:14b-q8_0`).
2. **Bohemia**:
   - personalidad más libre, literaria y directa;
   - menos escolar, rechaza el "humanismo barato" y busca la tensión latente;
   - usa `FUSION_READER_BOHEMIA_CHAT_MODEL` si está definido, o el default.

## Velos Conversacionales

Fusion Reader v2 introduce un cuarto eje liviano de modulación tonal: el **Velo**.
- Es ortogonal a Anclaje, Perfil y Razonamiento.
- Inyecta una micro-instrucción dinámica (ej: "Nocturna", "Desarme", "Crítica") al final de la capa de personalidad.
- No requiere recargar modelos ni reescribir prompts completos.
- *Default:* "Lucy" (vacío/neutro).

## Selección de Voz TTS

Fusion Reader v2 introduce un quinto eje de personalización: la **Voz**.
- Permite cambiar la voz neural (AllTalk/XTTS) en tiempo de ejecución.
- **Catálogo Dinámico:** Se nutre de las voces disponibles en el servidor AllTalk activo.
- **Etiquetas Mitológicas:** Los nombres de archivo se mapean a códigos mitológicos (ej: `female_03.wav` -> "● M03 — Hera", `Morgan_Freeman CC3.wav` -> "● V06 — Hermes") y se agrupan en "Voces M" y "Voces V" mediante `<optgroup>`. Se aplica un código de colores distintivo por voz para facilitar la identificación visual.
- **Persistencia:** La voz seleccionada se guarda en el estado de la sesión y persiste entre reinicios.
- **Sincronización:** Al cambiar de voz, se cancelan los prefetches y preparaciones activas para evitar la mezcla de voces en la cola de reproducción.

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
