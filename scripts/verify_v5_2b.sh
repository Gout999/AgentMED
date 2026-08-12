#!/usr/bin/env bash
# V5-2B Async Public Intents verifier.
#
# This gate is intentionally local/offline except for the explicitly
# disposable PostgreSQL database. The legacy Quality API live conformance
# suite is excluded because it requires a separately running demo-app and is
# not part of the V5-2B public-operation surface.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
CONTRACT_PYTHON="${CONTRACT_PYTHON:-$PYTHON}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

failures=0

run_section() {
    local name="$1"
    local description="$2"
    local function_name="$3"
    local rc
    echo "=== [$name] $description ==="
    "$function_name" >"$WORK_DIR/$name.log" 2>&1
    rc=$?
    cat "$WORK_DIR/$name.log"
    if [ "$rc" -eq 0 ]; then
        echo "[$name] PASS (exit $rc)"
    else
        echo "[$name] FAIL (exit $rc)"
        failures=$((failures + 1))
    fi
}

compiler_contracts() {
    cd "$ROOT" || return 1
    PYTHONPATH="$ROOT/contracts" "$CONTRACT_PYTHON" -m compiler check --json \
        && PYTHONPATH="$ROOT/contracts" "$CONTRACT_PYTHON" -m pytest \
            contracts/compiler/tests contracts/conformance \
            --ignore=contracts/conformance/test_quality_api.py -q
}

control_plane() {
    cd "$ROOT/control-plane" || return 1
    env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL \
        PYTHONPATH=. "$PYTHON" -m pytest tests/unit \
            tests/test_v5_c1_shadow_parity.py \
            tests/test_v5_c2_foundation.py \
            tests/test_v5_c2_graph.py \
            tests/test_v5_c3_import_graph.py \
            tests/test_v5_c4_allowlist_diff.py \
            tests/test_v5_c4_fallback_drill.py \
            tests/test_v5_c5_rollback_drill.py -q \
        && "$PYTHON" scripts/check_import_graph.py
}

cli_console() {
    cd "$ROOT/cli" || return 1
    PYTHONPATH=src "$PYTHON" -m pytest tests -q \
        && cd "$ROOT/console" \
        && npm test -- --run \
        && npm run build
}

postgres() {
    if [ "${CASELOOP_ALLOW_INTEGRATION_RESET:-}" != "true" ] \
        || [ -z "${DATABASE_URL:-}" ]; then
        echo "V5-2B PostgreSQL proof requires an explicitly disposable DATABASE_URL"
        return 2
    fi
    cd "$ROOT/control-plane" || return 1
    PYTHONPATH=. "$PYTHON" -m pytest tests/unit/test_migrations.py -k postgresql -q \
        && PYTHONPATH=. "$PYTHON" -m pytest \
            tests/integration/test_v5_lifecycle_authority_postgres.py \
            tests/integration/test_v5_application_catalog_postgres.py \
            tests/integration/test_v5_r2_manifest_activation_postgres.py \
            tests/integration/test_v5_system_versions_r3_postgres.py \
            tests/integration/test_v5_case_binding_r4_postgres.py \
            tests/integration/test_v5_work_kernel_postgres.py \
            tests/integration/test_v5_public_operations_postgres.py -q
}

run_section "1-contracts" "compiler and all non-live contract conformance" compiler_contracts
run_section "2-control-plane" "control-plane unit, wave, and import-boundary checks" control_plane
run_section "3-clients" "CLI full suite and Console test/build" cli_console
run_section "4-postgres" "disposable PostgreSQL migration and runtime matrix" postgres

echo
if [ "$failures" -eq 0 ]; then
    echo "verify_v5_2b: ALL SECTIONS PASS"
    exit 0
fi
echo "verify_v5_2b: FAIL ($failures section(s) failed)"
exit 1
