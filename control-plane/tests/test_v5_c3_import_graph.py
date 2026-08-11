"""C3 import-graph checker tests.

Covers the C3 required result "a mechanically checked module/lane and
allowed-import map" over the real ``control-plane/app`` tree:

- no module-level import cycles
- no function-level import cycles
- 100% module -> lane classification coverage
- no forbidden direction edges (V5 domain -> app.api; domain ->
  quality.client; v3-compat imports warn only)
- no direct-table-access violations (pg_advisory and bootstrap
  migration-version reads whitelisted)

Plus self-contained synthetic-tree tests of the checker's own detection
logic (cycle detection, direct-table-access, whitelist, CLI exit codes) so
the checker semantics are verified independently of the live tree.
"""
from __future__ import annotations

import sys
import ast
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
APP_DIR = SCRIPTS_DIR.parent / "app"

sys.path.insert(0, str(SCRIPTS_DIR))

import check_import_graph as cig  # noqa: E402


@pytest.fixture(scope="module")
def report() -> cig.Report:
    return cig.analyze(APP_DIR)


# --------------------------------------------------------------------------
# Live-tree checks
# --------------------------------------------------------------------------


def test_lane_coverage_is_complete(report: cig.Report) -> None:
    assert report.unclassified == [], (
        f"{len(report.unclassified)} modules have no lane: "
        f"{sorted(report.unclassified)}"
    )
    assert sum(report.lane_counts.values()) == len(report.modules)


def test_all_five_lanes_are_present(report: cig.Report) -> None:
    assert set(report.lane_counts) == {
        cig.LANE_V5_DOMAIN,
        cig.LANE_V4_COMPAT,
        cig.LANE_V3_COMPAT,
        cig.LANE_SHARED_FOUNDATION,
        cig.LANE_TRANSPORT,
    }


def test_no_module_level_cycles(report: cig.Report) -> None:
    assert report.module_cycles == [], (
        "module-level import cycles present:\n"
        + "\n".join(str(cycle) for cycle in report.module_cycles)
    )


def test_no_function_level_cycles(report: cig.Report) -> None:
    assert report.function_cycles == [], (
        "function-level import cycles present:\n"
        + "\n".join(str(cycle) for cycle in report.function_cycles)
    )


def test_no_forbidden_direction_fails(report: cig.Report) -> None:
    assert report.forbidden_fail == [], (
        "forbidden direction edges:\n"
        + "\n".join(str(edge) for edge in report.forbidden_fail)
    )


def test_v5_domain_never_imports_app_api(report: cig.Report) -> None:
    api_edges = [
        edge
        for edge in report.module_edges + report.function_edges
        if cig._lane_of(edge.src) == cig.LANE_V5_DOMAIN
        and (
            edge.dst == cig.API_MODULE_PREFIX
            or edge.dst.startswith(cig.API_MODULE_PREFIX + ".")
        )
    ]
    assert api_edges == []


def test_v3_quality_imports_are_warn_only(report: cig.Report) -> None:
    quality_edges = [
        edge
        for edge in report.module_edges + report.function_edges
        if edge.dst in cig.QUALITY_FORBIDDEN_MODULES
    ]
    assert quality_edges, "expected quality.client imports"
    v3_quality_sources = {
        edge.src
        for edge in quality_edges
        if cig.MODULE_LANES.get(edge.src) == cig.LANE_V3_COMPAT
    }
    # 现状：只有三个 V3 Scenario 服务 import quality.client（warn 不 fail）。
    assert v3_quality_sources == {
        "app.services.experiment_service",
        "app.services.gate_service",
        "app.services.release_service",
    }
    # 其他 lane 的 quality 边只允许出现在 transport（API/CLI/adapter 层）。
    for edge in quality_edges:
        lane = cig.MODULE_LANES.get(edge.src)
        assert lane in (cig.LANE_V3_COMPAT, cig.LANE_TRANSPORT), edge
    warn_sources = {edge.src for edge in report.forbidden_warn}
    assert warn_sources == v3_quality_sources


def test_no_direct_table_access_violations(report: cig.Report) -> None:
    assert report.dta_violations == [], (
        "direct-table-access violations:\n"
        + "\n".join(str(v) for v in report.dta_violations)
    )


def test_manifest_coordinator_does_not_construct_owned_catalog_rows() -> None:
    """Environment/edge persistence belongs to the catalog command port."""

    source = APP_DIR / "services" / "v5_manifest_import_coordinator.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"Environment", "DependencyEdge"}
    }
    assert constructed == set()
    text = source.read_text(encoding="utf-8")
    assert "self.catalog_commands.register_environment(" in text
    assert "self.catalog_commands.record_dependency_edge(" in text


def test_advisory_lock_calls_are_whitelisted(report: cig.Report) -> None:
    advisory = [
        entry
        for entry in report.dta_whitelisted
        if entry.detail.startswith("SELECT pg_advisory_xact_lock")
    ]
    assert len(advisory) == 7, advisory
    files = {entry.file for entry in advisory}
    assert files == {
        "app/bootstrap/stage1a_local.py",
        "app/bootstrap/v5_catalog_local.py",
        "app/services/application_catalog.py",
        "app/services/public_idempotency.py",
        "app/services/release_service.py",
        "app/services/signal_intake.py",
        "app/services/system_versions.py",
    }


def test_bootstrap_migration_version_reads_are_whitelisted(
    report: cig.Report,
) -> None:
    version_reads = [
        entry
        for entry in report.dta_whitelisted
        if entry.detail.startswith("SELECT version_num FROM alembic_version")
    ]
    assert len(version_reads) == 2, version_reads
    assert {entry.file for entry in version_reads} == {
        "app/bootstrap/stage1a_local.py",
        "app/bootstrap/v5_catalog_local.py",
    }


# --------------------------------------------------------------------------
# Synthetic-tree checks of the checker's own logic
# --------------------------------------------------------------------------


def _write_app(tmp_path: Path, files: dict[str, str]) -> Path:
    app_dir = tmp_path / "app"
    for name, content in files.items():
        path = app_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return app_dir


def _module_names(app_dir: Path) -> set[str]:
    return set(cig.collect_modules(app_dir))


def test_synthetic_module_cycle_detected(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "a.py": "from .b import bval\n",
            "b.py": "from .a import aval\n",
        },
    )
    modules = cig.collect_modules(app_dir)
    module_edges, function_edges = cig.build_edges(modules, app_dir)
    module_cycles, function_cycles = cig.find_cycles(
        module_edges, function_edges, modules
    )
    assert len(module_cycles) == 1, module_cycles
    assert set(module_cycles[0].members) == {
        f"{app_dir.name}.a",
        f"{app_dir.name}.b",
    }
    assert module_cycles[0].kind == "module"
    assert function_cycles == []


def test_synthetic_function_cycle_detected(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "c.py": "from .d import dval\n",
            "d.py": (
                "def f():\n"
                "    from .c import cval\n"
                "    return cval\n"
            ),
        },
    )
    modules = cig.collect_modules(app_dir)
    module_edges, function_edges = cig.build_edges(modules, app_dir)
    module_cycles, function_cycles = cig.find_cycles(
        module_edges, function_edges, modules
    )
    assert module_cycles == []
    assert len(function_cycles) == 1, function_cycles
    assert set(function_cycles[0].members) == {
        f"{app_dir.name}.c",
        f"{app_dir.name}.d",
    }
    assert any(
        edge.scope == "function" for edge in function_cycles[0].edges
    )


def test_synthetic_no_cycle_clean(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "x.py": "from .y import yval\n",
            "y.py": "yval = 1\n",
        },
    )
    modules = cig.collect_modules(app_dir)
    module_edges, function_edges = cig.build_edges(modules, app_dir)
    module_cycles, function_cycles = cig.find_cycles(
        module_edges, function_edges, modules
    )
    assert module_cycles == []
    assert function_cycles == []


def test_synthetic_dta_detects_table_crud(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "e.py": (
                "def touch(session, row):\n"
                "    session.execute(row.__table__.insert())\n"
            ),
        },
    )
    violations, whitelisted = cig.check_direct_table_access(
        cig.collect_modules(app_dir), app_dir
    )
    assert whitelisted == []
    assert len(violations) == 1, violations
    assert violations[0].kind == "table_crud"
    assert "__table__.insert" in violations[0].detail


def test_synthetic_dta_flags_raw_text(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "g.py": (
                "def wipe(session):\n"
                "    session.execute(sa.text('DELETE FROM x'))\n"
            ),
        },
    )
    violations, whitelisted = cig.check_direct_table_access(
        cig.collect_modules(app_dir), app_dir
    )
    assert whitelisted == []
    assert len(violations) == 1, violations
    assert violations[0].kind == "raw_text"
    assert "DELETE FROM x" in violations[0].detail


def test_synthetic_dta_whitelists_advisory_lock(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "h.py": (
                "def lock(session, key):\n"
                "    session.execute(\n"
                "        text('SELECT pg_advisory_xact_lock(:lock_key)'),\n"
                "        {'lock_key': key},\n"
                "    )\n"
            ),
        },
    )
    violations, whitelisted = cig.check_direct_table_access(
        cig.collect_modules(app_dir), app_dir
    )
    assert violations == []
    assert len(whitelisted) == 1, whitelisted
    assert whitelisted[0].detail == "SELECT pg_advisory_xact_lock(:lock_key)"


def test_synthetic_dta_ignores_execute_without_text(tmp_path: Path) -> None:
    app_dir = _write_app(
        tmp_path,
        {
            "i.py": (
                "def query(session):\n"
                "    return session.execute(select(X)).scalars()\n"
            ),
        },
    )
    violations, whitelisted = cig.check_direct_table_access(
        cig.collect_modules(app_dir), app_dir
    )
    assert violations == []
    assert whitelisted == []


def test_synthetic_cli_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clean = _write_app(tmp_path / "clean", {"clean.py": "value = 1\n"})
    cyclic = _write_app(
        tmp_path / "cyclic",
        {"a.py": "from .b import bval\n", "b.py": "from .a import aval\n"},
    )
    for app_dir in (clean, cyclic):
        for module in cig.collect_modules(app_dir):
            monkeypatch.setitem(cig.MODULE_LANES, module, cig.LANE_V5_DOMAIN)
    assert cig.main(["--app-dir", str(clean)]) == 0
    assert cig.main(["--app-dir", str(cyclic)]) == 1


def test_checker_is_importable_as_module() -> None:
    assert callable(cig.analyze)
    assert callable(cig.main)
