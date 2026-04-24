# Fusion / OpenClaw / SearXNG — Convivencia

## Regla madre

Antigravity/Doctora/Telegram es otro sistema de la máquina.

Fusion no debe arreglar sus problemas tocando:

- `OpenClaw main`
- Telegram
- Brave/global `web_search`
- gateway compartido de OpenClaw

## Estado actual

```text
Antigravity/Doctora/Telegram -> OpenClaw main
Fusion Reader v2 -> SearXNG local primero
Fusion Reader v2 -> OpenClaw fusion-research solo como fallback
```

## Por qué existe esta frontera

`main` y `fusion-research` tienen workspaces separados, pero OpenClaw comparte
gateway y capa web. Por eso tocar Brave/global web search para Fusion tiene
riesgo de interferencia.

## Decisión vigente

Para Fusion:

- provider default: `auto`
- `SearXNG` local primero
- `OpenClaw fusion-research` fallback
- `main` nunca

## Lo que no se hace

- no tocar `BRAVE_API_KEY`
- no ejecutar `openclaw configure --section web` para arreglar Fusion
- no mutar `openclaw-gateway.service`
- no usar Tavily en el flujo normal de Fusion

## Riesgo actual

- tocar `SearXNG` desde Fusion: bajo
- usar fallback `fusion-research`: medio
- tocar Brave/global `web_search`: alto

## Documento de referencia

La arquitectura de Fusion está en `docs/ARCHITECTURE.md`. Esta nota existe
solo para congelar la frontera con Antigravity.
