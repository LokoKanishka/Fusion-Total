# Seguimiento Fusion Reader

## Mision 01 - Auditoria de Biblioteca

- [x] Listar archivos en `library/`
- [x] Validar integridad de textos
- [x] Redactar reporte final

## Mision 02 - Fusion Reader v2 Voice First

- [x] Crear blueprint voice-first
- [x] Crear paquete `fusion_reader_v2/`
- [x] Integrar contrato TTS + AllTalkProvider
- [x] Agregar cache de audio y prefetch
- [x] Crear servidor v2 en puerto 8010
- [x] Crear UI web minima
- [x] Servir audio cacheado al navegador
- [x] Agregar modo continuo experimental
- [x] Documentar estado de continuidad
- [x] Exponer latencia basica por audio (`ready_ms`, `synthesis_ms`)
- [x] Persistir historial de latencia por chunk
- [x] Reemplazar lista visual de biblioteca por zona de carga
- [x] Agregar importador/conversor de documentos
- [x] Soportar PDF con `pdftotext`
- [x] Agregar subida binaria directa para PDFs grandes
- [x] Agregar OCR para PDF escaneado con Tesseract
- [x] Estructurar OCR escaneado por paginas, encabezados y columnas
- [x] Mejorar OCR escaneado con preprocesado de imagen y paginas en paralelo
- [x] Filtrar indices/portadas ruidosas y reparar errores comunes de OCR
- [x] Evitar que un prefetch TTS colgado bloquee la lectura indefinidamente
- [x] Corregir reporte de fallos TTS para que no aparezcan como `ok`
- [x] Guardar texto convertido liviano en runtime
- [x] Soportar DOCX/ODT con extraccion interna
- [x] Usar LibreOffice como respaldo para formatos de oficina
- [x] Mostrar progreso durante OCR de PDFs grandes
- [x] Mostrar resumen de latencia por documento/chunk
- [x] Crear entorno GPU aislado para RTX 5090
- [x] Verificar AllTalk GPU en puerto temporal 7852 con generacion real
- [x] Validar AllTalk GPU 7852 solo como medicion historica inicial
- [x] Reservar puerto GPU propio de Fusion en 7853 y dejar de autodetectar 7852
- [x] Agregar ventana de prefetch alrededor del cursor (`FUSION_READER_PREFETCH_AHEAD=3`)
- [x] Corregir URLs absolutas de AllTalk GPU que apuntaban al puerto 7851
- [x] Agregar modo de precache/preparar libro completo en background
- [x] Guardar decision: lectura aislada de conversacion LLM
- [x] Crear `ConversationCore` separado del motor de lectura
- [x] Exponer snapshot de contexto del lector para el LLM
- [x] Agregar endpoint `/api/chat` sin tocar `/api/read`
- [x] Crear UI minima de chat textual de laboratorio
- [x] Reorganizar UI: lectura arriba y laboratorio abajo
- [x] Documentar modo `Dialogar` en `FUSION_READER_V2_DIALOGUE.md`
- [x] Agregar contrato STT propio para v2
- [x] Agregar endpoints `/api/dialogue/status`, `/api/dialogue/turn`, `/api/dialogue/reset`
- [x] Agregar boton `Dialogar` con captura de microfono y barge-in inicial
- [x] Agregar servidor STT persistente GPU con faster-whisper
- [x] Usar STT server 8021 como principal y Whisper CLI solo como fallback

## Mision 03 - Orden y Estabilizacion

- [x] Recuperar memoria y contrastar con `AGENTS.md`/blueprint
- [x] Simplificar `FUSION_READER_V2_STATE.md` como hoja de continuidad actual
- [x] Reordenar este tablero para separar completado de pendientes reales
- [x] Ejecutar `python3 -m unittest tests.test_fusion_reader_v2 -v`
- [x] Reparar lanzador para levantar TTS antes de abrir Fusion
- [x] Levantar stack funcional actual: Fusion 8010, STT 8021, TTS fallback 7851
- [x] Ajustar captura de `Dialogar` con pre-roll y corte menos agresivo
- [x] Corregir `transcription_failed` por chunks WebM sin cabecera
- [x] Reemplazar captura WebM de `Dialogar` por PCM/WAV directo en navegador
- [x] Dar prioridad al diálogo sobre `Preparar documento`
- [x] Levantar stack preferido GPU: TTS GPU 7853 + reiniciar Fusion para usarlo
- [x] Blindar Fusion para no reclamar el puerto 7852 de otros agentes
- [x] Blindar Fusion para no aceptar un `Ready` en 7853 sin dueño `fusion_reader_v2`
- [x] Corregir frontera Doctora Lucy/Fusion: Lucy 7854, Fusion 7853, 7852 histórico
- [x] Blindar `AllTalkProvider` contra `7854` de Doctora y `7852` histórico aunque vengan por entorno
- [x] Corregir boot, bóveda SQLite y búnker JSONL de Doctora para no reinyectar 7851/7852
- [x] Agregar `scripts/verify_voice_port_isolation.sh` como verificador automático de frontera
- [x] Agregar columna derecha vacia para futuras herramientas sin comprimir lectura
- [x] Compactar zona de carga y tarjetas de notas para recuperar espacio util
- [x] Medir latencia de dialogo por tramo (`STT`, `chat`, `voz`, servidor)
- [x] Reducir latencia de notas y dialogo con confirmaciones `text_ack`
- [x] Agregar barge-in navegador para cortar voz local/audio al hablar encima
- [x] Filtrar alucinaciones STT tipo "suscribete" antes de UI/chat/notas
- [x] Saneamiento final de continuidad en README/STATE/DIALOGUE/AUDIT
- [x] Agregar documento principal + documentos de consulta en estado, API, UI y chat
- [x] Agregar trazas persistentes de `Dialogar` y degradacion controlada `Supremo -> Pensamiento` en voz
- [x] Crear workbook de personalidad profunda por modo en `FUSION_READER_V2_PERSONALITY_WORKBOOK.md`
- [x] Agregar `Modo libre` al laboratorio para desacoplar conversación y texto cuando haga falta
- [ ] Probar modo `Dialogar` con microfono real
- [ ] Ajustar VAD/barge-in segun ruido ambiente y eco
- [ ] Afinar warmup/keep-hot de AllTalk GPU
- [x] Diseñar y cablear personalidad por modo (`Normal`, `Pensamiento`, `Supremo`)
- [ ] Agregar pausa/reanudar como estado real de lectura
- [ ] Mejorar OCR fino para PDFs escaneados largos

## Mision 04 - Notas del Lector

- [x] Crear modelo/persistencia de notas por documento y bloque
- [x] Agregar endpoints para listar/crear/editar/borrar notas
- [x] Agregar panel lateral de notas debajo de `Preparar documento`
- [x] Hacer notas compactas y desplegables tipo lector PDF/Word
- [x] Agregar accion `Ir al bloque`
- [x] Detectar comando textual/voz `guardá esto como nota: ...`
- [x] Agregar tests de persistencia, vinculacion al bloque y comando de nota
- [x] Probar el panel con un documento real largo en navegador
- [x] Etiquetas compactas tipo `B45 idea breve`
- [x] Renombrar etiquetas de notas desde la UI
- [x] Usar bloque visible enviado por la UI para guardar notas por voz
