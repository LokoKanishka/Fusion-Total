#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$ROOT_DIR/config/project_isolation.env}"

if [[ -f "$CONFIG_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
  set +a
fi

PROJECT_ID="${FUSION_PROJECT_ID:-fusiontotal}"
EXPECTED_ROOT="${FUSION_REPO_ROOT:-$ROOT_DIR}"
N8N_CONTAINER="${FUSION_EXCLUSIVE_N8N_CONTAINER:-lucy_brain_n8n}"
N8N_PORT="${FUSION_EXCLUSIVE_N8N_PORT:-5678}"
RESERVED_CONTAINERS="${FUSION_RESERVED_CONTAINERS:-}"
EXCLUSIVE_PORTS="${FUSION_EXCLUSIVE_PORTS:-5678}"
CMD="${1:-check}"

fail() {
  echo "ISOLATION_GUARD_FAIL: $*" >&2
  exit 1
}

pass() {
  echo "ISOLATION_GUARD_OK: $*"
}

if [[ "$CMD" != "check" ]]; then
  echo "usage: $0 check" >&2
  exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "docker no disponible"
fi

if [[ "$ROOT_DIR" != "$EXPECTED_ROOT" ]]; then
  fail "repo activo '$ROOT_DIR' no coincide con root esperado '$EXPECTED_ROOT'"
fi

if [[ -n "${COMPOSE_PROJECT_NAME:-}" && "${COMPOSE_PROJECT_NAME}" != "$PROJECT_ID" ]]; then
  fail "COMPOSE_PROJECT_NAME='${COMPOSE_PROJECT_NAME}' != '${PROJECT_ID}'"
fi

declare -A RESERVED=()
IFS=',' read -r -a _containers <<<"$RESERVED_CONTAINERS"
for c in "${_containers[@]}"; do
  c="${c// /}"
  [[ -n "$c" ]] && RESERVED["$c"]=1
done

declare -A PORTS=()
IFS=',' read -r -a _ports <<<"$EXCLUSIVE_PORTS"
for p in "${_ports[@]}"; do
  p="${p// /}"
  [[ -n "$p" ]] && PORTS["$p"]=1
done

# 0) Contenedores reservados: si existen, deben pertenecer a este proyecto compose
for reserved_name in "${!RESERVED[@]}"; do
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$reserved_name"; then
    c_project="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$reserved_name" 2>/dev/null || true)"
    if [[ -n "$c_project" && "$c_project" != "$PROJECT_ID" ]]; then
      fail "contenedor reservado '$reserved_name' pertenece a otro proyecto compose: $c_project"
    fi
  fi
done

# 1) n8n exclusivo: permitir coexistencia en otros puertos/proyectos, pero no mezcla real
while IFS= read -r row; do
  [[ -z "$row" ]] && continue
  name="${row%% *}"
  image="${row#* }"
  lower_name="${name,,}"
  lower_image="${image,,}"
  if [[ "$lower_name" != *n8n* && "$lower_image" != *n8n* ]]; then
    continue
  fi
  if [[ -z "${RESERVED[$name]:-}" ]]; then
    # No debe exponer el puerto exclusivo de Fusion ni montar data de Fusion.
    if docker ps --filter "name=^${name}$" --format '{{.Ports}}' | grep -Eq "(^|,)\\s*(127\\.0\\.0\\.1:|0\\.0\\.0\\.0:)?${N8N_PORT}->"; then
      fail "contenedor n8n ajeno usa puerto exclusivo ${N8N_PORT}: $name"
    fi
    if docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$name" 2>/dev/null | grep -Fq "${EXPECTED_ROOT}/data/n8n"; then
      fail "contenedor n8n ajeno monta data de Fusion: $name"
    fi
  fi
done < <(docker ps --format '{{.Names}} {{.Image}}')

# 2) Puertos exclusivos: no aceptar publish por contenedores ajenos
while IFS= read -r row; do
  [[ -z "$row" ]] && continue
  name="${row%% *}"
  ports_raw="${row#* }"
  [[ -z "$ports_raw" ]] && continue
  while IFS= read -r hp; do
    [[ -z "$hp" ]] && continue
    if [[ -n "${PORTS[$hp]:-}" && -z "${RESERVED[$name]:-}" ]]; then
      fail "puerto exclusivo $hp publicado por contenedor ajeno: $name"
    fi
  done < <(printf '%s\n' "$ports_raw" | grep -oE '127\.0\.0\.1:[0-9]+->' | sed -E 's/^127\.0\.0\.1:([0-9]+)->$/\1/')
done < <(docker ps --format '{{.Names}} {{.Ports}}')

# 3) Puerto n8n: si hay listener, debe existir el contenedor exclusivo en running
if ss -ltnH "( sport = :$N8N_PORT )" | grep -q .; then
  if ! docker ps --format '{{.Names}}' | grep -Fxq "$N8N_CONTAINER"; then
    fail "puerto $N8N_PORT ocupado pero contenedor exclusivo $N8N_CONTAINER no está en running"
  fi
fi

pass "project=$PROJECT_ID root=$ROOT_DIR n8n_container=$N8N_CONTAINER n8n_port=$N8N_PORT"
