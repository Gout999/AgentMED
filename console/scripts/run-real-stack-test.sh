#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
console_dir="$(cd "${script_dir}/.." && pwd)"
repo_root="$(cd "${console_dir}/.." && pwd)"
control_plane_dir="${repo_root}/control-plane"
control_plane_venv="${AGENTMED_CONTROL_PLANE_VENV:-${control_plane_dir}/.venv}"
evidence_dir="${repo_root}/evidence/p0/p0-3-console"
audit_log="${evidence_dir}/audit.jsonl"
python_pycache_prefix="${AGENTMED_PYTHONPYCACHEPREFIX:-/tmp/agentmed-console-e2e-pycache-$$}"

if [[ ! -x "${control_plane_venv}/bin/alembic" || ! -x "${control_plane_venv}/bin/uvicorn" ]]; then
  echo "Control-plane environment is incomplete: ${control_plane_venv}" >&2
  exit 1
fi

db_name="agentmed_console_e2e_$(date +%s)_$$"
case "${db_name}" in
  agentmed_console_e2e_[0-9]*) ;;
  *)
    echo "Refusing unsafe scratch database name: ${db_name}" >&2
    exit 1
    ;;
esac

pg_host="${AGENTMED_TEST_PG_HOST:-127.0.0.1}"
pg_port="${AGENTMED_TEST_PG_PORT:-5432}"
pg_user="${AGENTMED_TEST_PG_USER:-agentmed}"
pg_password="${AGENTMED_TEST_PG_PASSWORD:-agentmed}"
pg_admin_host="${AGENTMED_TEST_PG_ADMIN_HOST:-/tmp}"
pg_admin_port="${AGENTMED_TEST_PG_ADMIN_PORT:-5432}"
pg_admin_user="${AGENTMED_TEST_PG_ADMIN_USER:-$(id -un)}"
pg_admin_database="${AGENTMED_TEST_PG_ADMIN_DATABASE:-postgres}"
pg_admin_password="${AGENTMED_TEST_PG_ADMIN_PASSWORD:-}"
database_url="postgresql+psycopg://${pg_user}:${pg_password}@${pg_host}:${pg_port}/${db_name}"
control_plane_pid=""
vite_pid=""

mkdir -p "${evidence_dir}"
rm -f "${audit_log}"

cleanup() {
  if [[ -n "${vite_pid}" ]] && kill -0 "${vite_pid}" 2>/dev/null; then
    kill "${vite_pid}" 2>/dev/null || true
    wait "${vite_pid}" 2>/dev/null || true
  fi
  if [[ -n "${control_plane_pid}" ]] && kill -0 "${control_plane_pid}" 2>/dev/null; then
    kill "${control_plane_pid}" 2>/dev/null || true
    wait "${control_plane_pid}" 2>/dev/null || true
  fi
  case "${db_name}" in
    agentmed_console_e2e_[0-9]*)
      PGPASSWORD="${pg_admin_password}" dropdb \
        --host="${pg_admin_host}" \
        --port="${pg_admin_port}" \
        --username="${pg_admin_user}" \
        --maintenance-db="${pg_admin_database}" \
        --if-exists "${db_name}" >/dev/null 2>&1 || true
      ;;
  esac
}
trap cleanup EXIT INT TERM

if lsof -nP -iTCP:18090 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 18090 is already in use; refusing to test against an unknown control-plane." >&2
  exit 1
fi
if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 5173 is already in use; refusing to test against an unknown Console." >&2
  exit 1
fi

PGPASSWORD="${pg_admin_password}" createdb \
  --host="${pg_admin_host}" \
  --port="${pg_admin_port}" \
  --username="${pg_admin_user}" \
  --maintenance-db="${pg_admin_database}" \
  --owner="${pg_user}" \
  "${db_name}"

(
  cd "${control_plane_dir}"
  DATABASE_URL="${database_url}" \
  AUDIT_JSONL_PATH="${audit_log}" \
  PYTHONPYCACHEPREFIX="${python_pycache_prefix}" \
  "${control_plane_venv}/bin/alembic" upgrade head
) >"${evidence_dir}/migration.log" 2>&1

(
  cd "${control_plane_dir}"
  export DATABASE_URL="${database_url}"
  export QUALITY_API_BASE_URL="http://127.0.0.1:9"
  export CONTROL_PLANE_TOKEN="console-e2e-control-token"
  export APPROVAL_AUTHORITY_TOKEN="console-e2e-approval-token"
  export AUDIT_JSONL_PATH="${audit_log}"
  export NOTIFICATION_ADAPTER="disabled"
  export NO_PROXY="*"
  export no_proxy="*"
  export PYTHONPYCACHEPREFIX="${python_pycache_prefix}"
  exec "${control_plane_venv}/bin/uvicorn" app.main:app --host 127.0.0.1 --port 18090
) >"${evidence_dir}/control-plane.log" 2>&1 &
control_plane_pid=$!

for _ in {1..80}; do
  if curl -fsS http://127.0.0.1:18090/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
if ! curl -fsS http://127.0.0.1:18090/healthz >/dev/null; then
  tail -n 80 "${evidence_dir}/control-plane.log" >&2
  exit 1
fi

(
  cd "${console_dir}"
  exec npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
) >"${evidence_dir}/vite.log" 2>&1 &
vite_pid=$!

for _ in {1..80}; do
  if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
if ! curl -fsS http://127.0.0.1:5173/ >/dev/null; then
  tail -n 80 "${evidence_dir}/vite.log" >&2
  exit 1
fi

cd "${console_dir}"
exec_status=0
unset NO_COLOR
npx playwright test --config playwright.config.ts || exec_status=$?
exit "${exec_status}"
