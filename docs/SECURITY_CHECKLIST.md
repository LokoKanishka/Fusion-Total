# Security checklist (skills/extensiones de terceros)

Antes de integrar:
- Licencia clara (ideal: MIT/Apache-2.0/BSD)
- Actividad (commits recientes, issues respondidos)
- Evitar installers opacos (curl|bash sin auditar, binarios sin source)
- Revisar:
  - shell-outs (`os.system`, `subprocess`, `exec`)
  - lectura/escritura de archivos
  - llamadas de red y endpoints
  - permisos y rutas peligrosas
- Pin por tag o commit hash
- Registrar en `docs/INTEGRATIONS.md`

## Hardening local de políticas
- `mode_safe` debe mantener `exec` denegado (no permitido en `allow`).
- Verificación rápida:
  - `bash -n scripts/mode_safe.sh scripts/policy_engine.sh`
  - `./scripts/policy_engine.sh check` (debe devolver `POLICY_ENGINE_OK`)

## Aislamiento estricto de proyecto (Fusion)
- No mezclar runtime/servicios con otros proyectos.
- `n8n` de Fusion es exclusivo (no compartir contenedor, volumen ni base con otros repos).
- Puerto `5678` reservado para Fusion (`n8n`) y bloqueante ante conflicto.
- Antes de `up/restart/stop/down`: ejecutar `./scripts/fusion_isolation_guard.sh check`.
- En gateway n8n, mantener routing/modelos y matriz MCP bajo archivo controlado:
  - `config/n8n_flow_routing.json`
  - `config/n8n_mcp_matrix.json`
  - `config/mcp_action_policies.json` (read/write/sensitive + aprobación humana)
