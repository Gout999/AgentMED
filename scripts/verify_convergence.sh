#!/usr/bin/env bash
#
# CaseLoop V5 convergence (C5) — unified verification entry point.
#
# This is the C5 enforcement gate for the v5 architecture-convergence waves:
# it runs every offline wave suite in order and fails the shell with a
# non-zero exit unless every section passes.  It only orchestrates existing
# commands; it changes no test, no code, and no generated artifact.
#
#   Interpreter:  ${PYTHON} (default python3; override, e.g.
#                 PYTHON=/path/to/venv/bin/python bash scripts/verify_convergence.sh)
#   Usage:        bash scripts/verify_convergence.sh
#   Exit:         0 if all sections PASS, 1 if any section FAILs.
#
# Sections:
#   1. compiler determinism — re-emit generated artifacts, require zero diff
#   2. compiler tests
#   3. contracts conformance (schemas, wilson, v4, v5)
#   4. control-plane unit tests + C1–C4 wave checkers
#   5. import-graph checker (C3)
#   6. cli tests
#
# Path notes (kept faithful to the C5 plan while making each command runnable
# from the repository root):
#   - ``python -m compiler`` and the compiler tests need ``contracts/`` on the
#     import path, so PYTHONPATH is anchored at $ROOT/contracts (the plan's
#     bare ``PYTHONPATH=contracts`` only resolves relative to the caller's
#     cwd, which fails inside contracts/compiler/).
#   - the determinism diff is anchored at the repository root with
#     ``git -C $ROOT``; a cwd-relative pathspec inside contracts/compiler/
#     resolves to a non-existent path and dies with exit 128.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

failures=0

# run_section <name> <description> <function>
#   Runs the section function, captures its exit code, prints PASS/FAIL with
#   the exit code, and on failure shows the captured output tail.
run_section() {
    local name="$1"
    local desc="$2"
    local fn="$3"
    local rc
    echo "=== [$name] $desc ==="
    "$fn" >"$WORK_DIR/$name.log" 2>&1
    rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "[$name] PASS (exit $rc)"
    else
        echo "[$name] FAIL (exit $rc)"
        tail -n 50 "$WORK_DIR/$name.log"
        failures=$((failures + 1))
    fi
}

s1_compiler_determinism() {
    cd "$ROOT/contracts/compiler" || return 1
    PYTHONPATH="$ROOT/contracts" "$PYTHON" -m compiler emit \
        && git -C "$ROOT" diff --exit-code -- contracts/v5/generated/
}

s2_compiler_tests() {
    cd "$ROOT/contracts/compiler" || return 1
    PYTHONPATH="$ROOT/contracts" "$PYTHON" -m pytest tests
}

s3_conformance() {
    cd "$ROOT/contracts" || return 1
    "$PYTHON" -m pytest conformance/test_schemas.py conformance/test_wilson.py \
        conformance/test_v4_*.py conformance/test_v5_*.py
}

s4_control_plane() {
    cd "$ROOT/control-plane" || return 1
    "$PYTHON" -m pytest tests/unit tests/test_v5_c1_shadow_parity.py \
        tests/test_v5_c2_foundation.py tests/test_v5_c2_graph.py \
        tests/test_v5_c3_import_graph.py tests/test_v5_c4_allowlist_diff.py \
        tests/test_v5_c4_fallback_drill.py
}

s5_import_graph() {
    cd "$ROOT/control-plane" || return 1
    "$PYTHON" scripts/check_import_graph.py
}

s6_cli() {
    cd "$ROOT/cli" || return 1
    "$PYTHON" -m pytest tests
}

run_section "1-compiler-determinism" "compiler emit + zero diff on contracts/v5/generated/" s1_compiler_determinism
run_section "2-compiler-tests" "compiler test suite" s2_compiler_tests
run_section "3-conformance" "contracts conformance (schemas/wilson/v4/v5)" s3_conformance
run_section "4-control-plane" "control-plane unit tests + C1-C4 wave checkers" s4_control_plane
run_section "5-import-graph" "import-graph checker (C3)" s5_import_graph
run_section "6-cli" "cli test suite" s6_cli

echo
if [ "$failures" -eq 0 ]; then
    echo "verify_convergence: ALL SECTIONS PASS"
    exit 0
else
    echo "verify_convergence: FAIL ($failures section(s) failed)"
    exit 1
fi
