# Biblioteca — Auditoría 2026-04-24

## Resumen

Se revisaron 8 archivos bajo `library/`.

Clasificación:

- conservar: 4
- conservar como muestra: 1
- conservar como metadata/runtime visible: 3
- archivar ahora: 0
- dudosos: 0

No se eliminó nada.

## Inventario

### Conservar

- `library/1cunn.txt`
  - muestra corta y legible de lectura básica
- `library/diego_audio.txt`
  - útil para lectura audible e interrupción
- `library/largo_test.txt`
  - fixture clara para paginación/navegación
- `library/seek_test.txt`
  - fixture mínima para búsqueda/seek

### Conservar como muestra

- `library/uploads/documento_subido.txt`
  - útil para demostrar flujo de importación/subida desde navegador
  - no parece basura, pero es muestra de laboratorio más que fixture principal

### Conservar como metadata/runtime visible

- `library/notes/largo_test/page_1.json`
- `library/notes/largo_test/page_3.json`
- `library/notes/seek_test/page_1.json`

Sirven como ejemplo de persistencia de notas sobre fixtures pequeños. No mover
sin revisar antes si algún test o demo local depende de ellos.

## Conclusión

La biblioteca visible es pequeña y no está llena de basura obvia. Hoy conviene
mantenerla, documentarla y evitar una limpieza agresiva. Si más adelante crece,
recién ahí tendría sentido crear `library/_archive/`.
