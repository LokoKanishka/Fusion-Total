# n8n Model + MCP Routing (Fusion)

## Objetivo
- Enrutar cada evento de `lucy-input`/`voice-input` hacia un perfil de ejecución.
- Forzar uso de modelos permitidos (`5.1 mini` y `14b`).
- Inyectar política MCP por perfil dentro del envelope que se escribe a `ipc/inbox`.

## Configuración base
- Routing de perfiles: `config/n8n_flow_routing.json`
- Matriz MCP por perfil: `config/n8n_mcp_matrix.json`
- Política por acción MCP: `config/mcp_action_policies.json`
- Aplicación al workflow: `./scripts/n8n_patch_lucy_gateway_v1.sh`

## Perfiles de routing
- `default`: fallback general.
- `automation`: flujos de n8n/orquestación.
- `coding`: tareas de código (prioriza `qwen2.5-coder:14b-instruct-q8_0`).
- `research`: investigación/consulta externa.
- `voice`: eventos de voz.

## Selección de perfil
Orden de prioridad:
1. `meta.workflow_profile` / `meta.route_profile` / `meta.profile` (si coincide con config).
2. Heurística por `kind`, `source`, `meta.task_type`, `meta.intent`, `text`.
3. Fallback a `default`.

## Inyección MCP
El gateway agrega en `payload.meta`:
- `routing`: perfil + modelo + fallback + backend + mcp_profile.
- `mcp`: perfil MCP + servers permitidos + modo de aprobación + tools sensibles.

También lo replica en el envelope top-level:
- `routing`
- `mcp`
- `mcp_approval`

## Aprobación humana por tipo de operación
- El gateway clasifica acciones MCP en `read`, `write`, `sensitive`.
- Entrada esperada para evaluación:
  - `meta.mcp_actions` (lista o CSV)
  - `meta.approved_by`
  - `meta.approval_token`
- Regla activa:
  - `read`: no requiere aprobación.
  - `write`/`sensitive`: aprobación humana obligatoria según `approval_mode`.
- Si falta aprobación en una acción que la requiere:
  - ACK `ok=false`
  - `reason=human_approval_required_for_mcp_actions`
  - evento a deadletter.

## Observabilidad
- Métricas por evento en `ipc/metrics/lucy_gateway_events.jsonl`.
- Snapshot:
  - `./scripts/n8n_observability_snapshot.sh`
- Dashboard:
  - `./scripts/ops_dashboard.sh`

## Validaciones rápidas
- `./scripts/n8n_mcp_preflight.sh`
- `./scripts/n8n_gateway_e2e.sh`
- `./scripts/n8n_approval_probe.sh`
- `./scripts/webhook_smoke.sh`
