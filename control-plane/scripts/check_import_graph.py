"""CaseLoop V5 C3 — import-graph, lane-coverage and direct-table-access checker.

Mechanically checks every module under ``control-plane/app/**/*.py``
(``__pycache__`` excluded) and reports/decides:

1. module-level import cycles          -> FAIL (exit 1)
2. function-level import cycles        -> FAIL (exit 1)
3. lane coverage (every module maps)   -> FAIL (exit 1)
4. forbidden direction edges           -> FAIL, or WARN for the v3-compat
                                          exception on ``quality.client``
5. direct table access                 -> FAIL (exit 1)

Module -> lane classification follows the C3 research matrix in
``docs/plans/v5-architecture-convergence.md`` §2.2:

- V5 domain: V5 models/contracts; Application Catalog, System Version,
  Case Binding, Acceptance, issue source and later admitted stage modules
  (``app/models/v5_tables.py``, ``app/services/{application_catalog,
  system_versions,case_binding,acceptance,issue_source,v5_authority,
  v5_lifecycle_authority,v5_manifest_import_coordinator,v5_application_list,
  }``, ``app/public_api/v5_models.py``).
- V4 compatibility: V4 models/contracts and the preserved public façade
  (``app/models/v4_tables.py``, ``app/public_api/*`` except
  ``credential_resolver`` and the V5 capability/transport models,
  ``app/services/{authority,v4_event_store,public_read,signal_intake}``).
- V3 compatibility: existing Scenario tables/routes/services/replay
  (``app/models/tables.py``, ``app/services/{audit,attribution,b1_fixture,
  case_service,case_closure_service,changeset_service,event_store,
  experiment_service,gate_service,lease,notification_service,outbox_relay,
  release_service,state_machines,trust_service}``).
- Shared foundation: database UoW, config, credential resolution, audit,
  idempotency, event/outbox primitives, receipt/integrity and
  canonicalization (``app/db.py``, ``app/config.py``, ``app/foundation/*``,
  ``app/utils/*``, ``app/services/{v4_audit,public_idempotency}``,
  ``app/public_api/credential_resolver.py``, package ``__init__`` modules).
- Transport: API/CLI/Console, capability discovery, read projections and
  provider adapters (``app/api/*``, ``app/main.py``, ``app/quality/*``,
  ``app/notifications/*``, ``app/workers/*``, ``app/bootstrap/*``,
  ``app/services/{read_views,v5_capabilities}``,
  ``app/public_api/v5_capability_models.py``).

Forbidden directions: a V5-domain module must not import ``app.api``; domain
modules (V5 domain and V4 compat) must not import ``app.quality``/client —
the v3-compat lane is reported as WARN instead of FAIL.

Direct-table-access scans ``<expr>.execute(text(...))`` calls (any receiver:
session/connection/engine) and ``<expr>.__table__.insert/update/delete()``
calls.  ``text()`` statements whose SQL starts with one of the whitelisted
prefixes are known infrastructure calls and are not violations:

- ``SELECT pg_advisory_xact_lock`` — advisory locks used by lease/idempotency
  and bootstrap serialization (7 call sites today);
- ``SELECT version_num FROM alembic_version`` — read-only migration-version
  check in the bootstrap entrypoints (2 call sites); not a domain
  cross-owner access.

The checker is importable (``import check_import_graph``) for tests and runs
as a CLI when executed directly.

Usage:
    python scripts/check_import_graph.py [--app-dir PATH]
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import DefaultDict, Iterable, Iterator, Sequence

LANE_V5_DOMAIN = "v5_domain"
LANE_V4_COMPAT = "v4_compat"
LANE_V3_COMPAT = "v3_compat"
LANE_SHARED_FOUNDATION = "shared_foundation"
LANE_TRANSPORT = "transport"

LANE_NAMES = {
    LANE_V5_DOMAIN: "V5 domain",
    LANE_V4_COMPAT: "V4 compat",
    LANE_V3_COMPAT: "V3 compat",
    LANE_SHARED_FOUNDATION: "shared foundation",
    LANE_TRANSPORT: "transport",
}

# --------------------------------------------------------------------------
# Module -> lane map (C3 research matrix, see module docstring).
# Every module under app/ must appear here exactly once.
# --------------------------------------------------------------------------
MODULE_LANES: dict[str, str] = {
    "app": LANE_SHARED_FOUNDATION,
    "app.config": LANE_SHARED_FOUNDATION,
    "app.db": LANE_SHARED_FOUNDATION,
    "app.main": LANE_TRANSPORT,
    "app.bootstrap": LANE_TRANSPORT,
    "app.bootstrap.stage1a_local": LANE_TRANSPORT,
    "app.bootstrap.v5_catalog_local": LANE_TRANSPORT,
    "app.models": LANE_SHARED_FOUNDATION,
    "app.models.tables": LANE_V3_COMPAT,
    "app.models.v4_tables": LANE_V4_COMPAT,
    "app.models.v5_tables": LANE_V5_DOMAIN,
    "app.models.v5_work_tables": LANE_V5_DOMAIN,
    "app.api": LANE_TRANSPORT,
    "app.api.cases": LANE_TRANSPORT,
    "app.api.changesets": LANE_TRANSPORT,
    "app.api.deps": LANE_TRANSPORT,
    "app.api.evidence_export": LANE_TRANSPORT,
    "app.api.experiments": LANE_TRANSPORT,
    "app.api.gates": LANE_TRANSPORT,
    "app.api.notifications": LANE_TRANSPORT,
    "app.api.public_v4": LANE_TRANSPORT,
    "app.api.public_v5": LANE_TRANSPORT,
    "app.api.read_views": LANE_TRANSPORT,
    "app.api.releases": LANE_TRANSPORT,
    "app.api.v5_route_registry": LANE_TRANSPORT,
    "app.foundation": LANE_SHARED_FOUNDATION,
    "app.foundation.bindings": LANE_SHARED_FOUNDATION,
    "app.foundation.events": LANE_SHARED_FOUNDATION,
    "app.foundation.graph": LANE_SHARED_FOUNDATION,
    "app.foundation.records": LANE_SHARED_FOUNDATION,
    "app.foundation.receipts": LANE_SHARED_FOUNDATION,
    "app.notifications": LANE_TRANSPORT,
    "app.notifications.adapters": LANE_TRANSPORT,
    "app.public_api": LANE_V4_COMPAT,
    "app.public_api.auth_contract": LANE_V4_COMPAT,
    "app.public_api.credential_resolver": LANE_SHARED_FOUNDATION,
    "app.public_api.errors": LANE_V4_COMPAT,
    "app.public_api.models": LANE_V4_COMPAT,
    "app.public_api.v2_contract": LANE_V4_COMPAT,
    "app.public_api.v5_capability_models": LANE_TRANSPORT,
    "app.public_api.v5_generated_wire": LANE_TRANSPORT,
    "app.public_api.v5_models": LANE_V5_DOMAIN,
    "app.quality": LANE_TRANSPORT,
    "app.quality.client": LANE_TRANSPORT,
    "app.services": LANE_SHARED_FOUNDATION,
    "app.services.acceptance": LANE_V5_DOMAIN,
    "app.services.application_catalog": LANE_V5_DOMAIN,
    "app.services.attribution": LANE_V3_COMPAT,
    "app.services.audit": LANE_V3_COMPAT,
    "app.services.authority": LANE_V4_COMPAT,
    "app.services.b1_fixture": LANE_V3_COMPAT,
    "app.services.case_binding": LANE_V5_DOMAIN,
    "app.services.case_closure_service": LANE_V3_COMPAT,
    "app.services.case_service": LANE_V3_COMPAT,
    "app.services.changeset_service": LANE_V3_COMPAT,
    "app.services.event_store": LANE_V3_COMPAT,
    "app.services.experiment_service": LANE_V3_COMPAT,
    "app.services.gate_service": LANE_V3_COMPAT,
    "app.services.issue_source": LANE_V5_DOMAIN,
    "app.services.lease": LANE_V3_COMPAT,
    "app.services.notification_service": LANE_V3_COMPAT,
    "app.services.outbox_relay": LANE_V3_COMPAT,
    "app.services.public_idempotency": LANE_SHARED_FOUNDATION,
    "app.services.public_read": LANE_V4_COMPAT,
    "app.services.read_views": LANE_TRANSPORT,
    "app.services.release_service": LANE_V3_COMPAT,
    "app.services.signal_intake": LANE_V4_COMPAT,
    "app.services.state_machines": LANE_V3_COMPAT,
    "app.services.system_versions": LANE_V5_DOMAIN,
    "app.services.trust_service": LANE_V3_COMPAT,
    "app.services.v4_audit": LANE_SHARED_FOUNDATION,
    "app.services.v4_event_store": LANE_V4_COMPAT,
    "app.services.v5_application_list": LANE_V5_DOMAIN,
    "app.services.v5_authority": LANE_V5_DOMAIN,
    "app.services.v5_capabilities": LANE_TRANSPORT,
    "app.services.v5_catalog_composition": LANE_V5_DOMAIN,
    "app.services.v5_composition": LANE_V5_DOMAIN,
    "app.services.v5_lifecycle_authority": LANE_V5_DOMAIN,
    "app.services.v5_manifest_import_coordinator": LANE_V5_DOMAIN,
    "app.utils": LANE_SHARED_FOUNDATION,
    "app.utils.ids": LANE_SHARED_FOUNDATION,
    "app.utils.jcs": LANE_SHARED_FOUNDATION,
    "app.utils.pii": LANE_SHARED_FOUNDATION,
    "app.utils.v4_integrity": LANE_SHARED_FOUNDATION,
    "app.utils.v5_integrity": LANE_SHARED_FOUNDATION,
    "app.workers": LANE_TRANSPORT,
    "app.workers.outbox": LANE_TRANSPORT,
}

# Lanes whose modules may never import the quality client.  V3 compat may
# still do so but is reported as WARN only (historical Scenario path).
DOMAIN_LANES_FORBIDDING_QUALITY = frozenset({LANE_V5_DOMAIN, LANE_V4_COMPAT})
QUALITY_WARN_LANE = LANE_V3_COMPAT
QUALITY_FORBIDDEN_MODULES = frozenset({"app.quality", "app.quality.client"})

# V5 domain must not import any transport route module under app.api.
API_MODULE_PREFIX = "app.api"

# text() statements exempt from the direct-table-access check.  A statement is
# whitelisted when its literal SQL starts with one of these prefixes.
TEXT_WHITELIST_PREFIXES: tuple[str, ...] = (
    "SELECT pg_advisory_xact_lock",
    "SELECT version_num FROM alembic_version",
)

# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    lineno: int
    scope: str  # "module" | "function"
    file: str

    def __str__(self) -> str:
        return f"{self.file}:{self.lineno}: {self.src} -> {self.dst} [{self.scope}]"


@dataclass(frozen=True)
class Cycle:
    members: tuple[str, ...]
    edges: tuple[Edge, ...]
    kind: str  # "module" | "function"

    def __str__(self) -> str:
        member_list = ", ".join(self.members)
        return f"{self.kind}-level cycle {{{member_list}}}:\n" + "\n".join(
            f"    {edge}" for edge in self.edges
        )


@dataclass(frozen=True)
class DirectTableAccess:
    file: str
    lineno: int
    kind: str  # "table_crud" | "raw_text"
    detail: str
    source: str

    def __str__(self) -> str:
        return f"{self.file}:{self.lineno}: {self.kind} {self.detail!r} [{self.source}]"


@dataclass
class Report:
    app_dir: Path
    modules: list[str] = field(default_factory=list)
    module_edges: list[Edge] = field(default_factory=list)
    function_edges: list[Edge] = field(default_factory=list)
    module_cycles: list[Cycle] = field(default_factory=list)
    function_cycles: list[Cycle] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    lane_counts: dict[str, int] = field(default_factory=dict)
    forbidden_fail: list[Edge] = field(default_factory=list)
    forbidden_warn: list[Edge] = field(default_factory=list)
    dta_violations: list[DirectTableAccess] = field(default_factory=list)
    dta_whitelisted: list[DirectTableAccess] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(
            self.module_cycles
            or self.function_cycles
            or self.unclassified
            or self.forbidden_fail
            or self.dta_violations
        )

    @property
    def warned(self) -> bool:
        return bool(self.forbidden_warn)


# --------------------------------------------------------------------------
# File / module discovery
# --------------------------------------------------------------------------


def collect_modules(app_dir: Path) -> dict[str, Path]:
    """Map dotted module names (e.g. ``app.services.v4_audit``) to files.

    Every ``*.py`` under ``app_dir`` (``__pycache__`` excluded) becomes a
    node, including ``__init__.py`` files (as ``app.package``).  Module names
    are prefixed with the app package name (``app``) so imports resolve
    against the ``app.*`` module map.
    """
    modules: dict[str, Path] = {}
    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(app_dir)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        modules[".".join([app_dir.name, *parts])] = path
    return modules


def _resolve_import_targets(
    node: ast.Import | ast.ImportFrom,
    current_module: str,
    modules: dict[str, Path],
) -> list[str]:
    """Resolve one import node to app-internal target module names.

    Non-app targets (stdlib, third-party) are skipped.  ``from app.x import
    y`` resolves to ``app.x.y`` when that module exists, otherwise to the
    package ``app.x``.  Relative imports resolve against ``current_module``.
    """
    targets: list[str] = []

    def app_internal(name: str) -> bool:
        return name == "app" or name.startswith("app.")

    if isinstance(node, ast.Import):
        for alias in node.names:
            if app_internal(alias.name):
                targets.append(alias.name)
        return targets

    assert isinstance(node, ast.ImportFrom)
    module = node.module or ""
    if node.level > 0:
        parts = current_module.split(".")
        if node.level <= len(parts):
            base = ".".join(parts[: -node.level])
        else:
            base = ""
        full = f"{base}.{module}" if module else base
    else:
        full = module
    if not app_internal(full):
        return targets
    for alias in node.names:
        if alias.name == "*":
            targets.append(full)
            continue
        candidate = f"{full}.{alias.name}"
        targets.append(candidate if candidate in modules else full)
    return targets


def _function_body_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """All import nodes that appear inside a function body (any depth)."""
    found: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    found.append(sub)
    return found


def _display_path(app_dir: Path, path: Path) -> str:
    """``app/services/x.py``-style path relative to the app package root."""
    return str(app_dir.name / path.relative_to(app_dir))


def build_edges(modules: dict[str, Path], app_dir: Path) -> tuple[list[Edge], list[Edge]]:
    """Collect module-level and function-level import edges.

    Returns ``(module_edges, function_edges)``.  An import inside any
    function body is a function-scope edge; top-level imports are
    module-scope edges.
    """
    module_edges: list[Edge] = []
    function_edges: list[Edge] = []
    seen: set[tuple[str, str, int, str]] = set()

    def record(scope: str, src: str, dst: str, lineno: int, file: str) -> None:
        key = (src, dst, lineno, scope)
        if key in seen:
            return
        seen.add(key)
        edge = Edge(src=src, dst=dst, lineno=lineno, scope=scope, file=file)
        (module_edges if scope == "module" else function_edges).append(edge)

    for module_name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError) as exc:
            raise RuntimeError(f"cannot parse {path}: {exc}") from exc
        top_level = {
            id(node)
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        function_level = {
            id(node) for node in _function_body_imports(tree)
        } - top_level
        display_file = _display_path(app_dir, path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if id(node) not in top_level | function_level:
                continue
            scope = "module" if id(node) in top_level else "function"
            for target in _resolve_import_targets(node, module_name, modules):
                record(scope, module_name, target, node.lineno, display_file)
    return module_edges, function_edges


# --------------------------------------------------------------------------
# Cycle detection (SCC)
# --------------------------------------------------------------------------


def _sccs(edges: Iterable[Edge], nodes: Iterable[str]) -> list[list[str]]:
    """Tarjan strongly-connected components over the directed edge set."""
    adjacency: DefaultDict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[edge.src].append(edge.dst)
    all_nodes = set(nodes)
    for edge in edges:
        all_nodes.add(edge.src)
        all_nodes.add(edge.dst)

    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index_counter
        indices[node] = index_counter
        lowlink[node] = index_counter
        index_counter += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in adjacency.get(node, ()):
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlink[node] = min(lowlink[node], lowlink[neighbor])
            elif neighbor in on_stack:
                lowlink[node] = min(lowlink[node], indices[neighbor])
        if lowlink[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(component)

    for node in sorted(all_nodes):
        if node not in indices:
            strongconnect(node)
    return components


def find_cycles(
    module_edges: Sequence[Edge],
    function_edges: Sequence[Edge],
    modules: dict[str, Path],
) -> tuple[list[Cycle], list[Cycle]]:
    """Return ``(module_cycles, function_cycles)``.

    A module-level cycle is an SCC of the module-level edge graph with more
    than one member or a self loop.  A function-level cycle is an SCC of the
    combined graph (module + function edges) that contains at least one
    function-scope edge; it is what survives after module-level imports are
    safe.
    """
    module_cycles: list[Cycle] = []
    for component in _sccs(module_edges, modules):
        members = tuple(sorted(component))
        if len(members) < 2:
            continue
        inner = [e for e in module_edges if e.src in component and e.dst in component]
        module_cycles.append(Cycle(members=members, edges=tuple(inner), kind="module"))

    combined = list(module_edges) + list(function_edges)
    function_cycles: list[Cycle] = []
    for component in _sccs(combined, modules):
        members = tuple(sorted(component))
        if len(members) < 2:
            continue
        inner = [e for e in combined if e.src in component and e.dst in component]
        if any(e.scope == "function" for e in inner):
            function_cycles.append(Cycle(members=members, edges=tuple(inner), kind="function"))
    return module_cycles, function_cycles


# --------------------------------------------------------------------------
# Lane coverage and forbidden directions
# --------------------------------------------------------------------------


def _lane_of(module: str) -> str | None:
    return MODULE_LANES.get(module)


def check_lanes(modules: dict[str, Path]) -> tuple[list[str], dict[str, int]]:
    unclassified = [m for m in modules if _lane_of(m) is None]
    counts: dict[str, int] = defaultdict(int)
    for module in modules:
        lane = _lane_of(module)
        if lane is not None:
            counts[lane] += 1
    return unclassified, dict(counts)


def check_forbidden_directions(
    module_edges: Sequence[Edge],
    function_edges: Sequence[Edge],
) -> tuple[list[Edge], list[Edge]]:
    """Return ``(fails, warns)`` for forbidden direction edges."""
    fails: list[Edge] = []
    warns: list[Edge] = []
    for edge in list(module_edges) + list(function_edges):
        lane = _lane_of(edge.src)
        if lane is None:
            continue
        if lane == LANE_V5_DOMAIN and (
            edge.dst == API_MODULE_PREFIX or edge.dst.startswith(API_MODULE_PREFIX + ".")
        ):
            fails.append(edge)
            continue
        if edge.dst in QUALITY_FORBIDDEN_MODULES:
            if lane in DOMAIN_LANES_FORBIDDING_QUALITY:
                fails.append(edge)
            elif lane == QUALITY_WARN_LANE:
                warns.append(edge)
    return fails, warns


# --------------------------------------------------------------------------
# Direct table access
# --------------------------------------------------------------------------


def _call_text_arg(call: ast.Call) -> ast.Call | None:
    """The ``text(...)`` call used as the statement argument of execute()."""
    if call.args and _is_text_call(call.args[0]):
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg in ("statement", "sql", "clause") and _is_text_call(keyword.value):
            return keyword.value
    return None


def _is_text_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "text"
    if isinstance(func, ast.Attribute):
        return func.attr == "text"
    return False


def _literal_sql(text_call: ast.Call) -> str | None:
    if text_call.args and isinstance(text_call.args[0], ast.Constant) and isinstance(
        text_call.args[0].value, str
    ):
        return text_call.args[0].value
    return None


def check_direct_table_access(
    modules: dict[str, Path], app_dir: Path
) -> tuple[list[DirectTableAccess], list[DirectTableAccess]]:
    """Scan for ``__table__.insert/update/delete`` and ``execute(text(...))``.

    Returns ``(violations, whitelisted)``.  ``text()`` statements whose
    literal SQL starts with a whitelist prefix are recorded as whitelisted,
    everything else (including non-literal SQL) is a violation.
    """
    violations: list[DirectTableAccess] = []
    whitelisted: list[DirectTableAccess] = []

    for module_name, path in modules.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, OSError) as exc:
            raise RuntimeError(f"cannot parse {path}: {exc}") from exc
        rel_file = _display_path(app_dir, path)
        source_lines = path.read_text(encoding="utf-8").splitlines()

        def source_line(lineno: int) -> str:
            if 1 <= lineno <= len(source_lines):
                return source_lines[lineno - 1].strip()
            return ""

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # __table__.insert() / update() / delete()
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("insert", "update", "delete")
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "__table__"
            ):
                violations.append(
                    DirectTableAccess(
                        file=rel_file,
                        lineno=node.lineno,
                        kind="table_crud",
                        detail=f"__table__.{func.attr}()",
                        source=source_line(node.lineno),
                    )
                )
                continue
            # <receiver>.execute(text(...))
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "execute"
            ):
                text_call = _call_text_arg(node)
                if text_call is None:
                    continue
                sql = _literal_sql(text_call)
                entry = DirectTableAccess(
                    file=rel_file,
                    lineno=node.lineno,
                    kind="raw_text",
                    detail=sql if sql is not None else "<non-literal text()>",
                    source=source_line(node.lineno),
                )
                if sql is not None and sql.startswith(TEXT_WHITELIST_PREFIXES):
                    whitelisted.append(entry)
                else:
                    violations.append(entry)
    return violations, whitelisted


# --------------------------------------------------------------------------
# Analysis entry point
# --------------------------------------------------------------------------


def analyze(app_dir: Path | str) -> Report:
    """Run every check over the app tree and return the combined report."""
    root = Path(app_dir).resolve()
    modules = collect_modules(root)
    module_edges, function_edges = build_edges(modules, root)
    module_cycles, function_cycles = find_cycles(module_edges, function_edges, modules)
    unclassified, lane_counts = check_lanes(modules)
    forbidden_fail, forbidden_warn = check_forbidden_directions(
        module_edges, function_edges
    )
    dta_violations, dta_whitelisted = check_direct_table_access(modules, root)
    return Report(
        app_dir=root,
        modules=sorted(modules),
        module_edges=module_edges,
        function_edges=function_edges,
        module_cycles=module_cycles,
        function_cycles=function_cycles,
        unclassified=unclassified,
        lane_counts=lane_counts,
        forbidden_fail=forbidden_fail,
        forbidden_warn=forbidden_warn,
        dta_violations=dta_violations,
        dta_whitelisted=dta_whitelisted,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _render(report: Report) -> str:
    lines: list[str] = []
    lines.append(f"app dir: {report.app_dir}")
    lines.append(f"modules: {len(report.modules)}")
    lines.append(f"module-scope edges: {len(report.module_edges)}")
    lines.append(f"function-scope edges: {len(report.function_edges)}")

    lines.append("")
    lines.append("lane coverage:")
    if report.unclassified:
        for module in sorted(report.unclassified):
            lines.append(f"  FAIL unclassified: {module}")
    else:
        for lane in sorted(LANE_NAMES):
            lines.append(f"  {LANE_NAMES[lane]}: {report.lane_counts.get(lane, 0)}")
        lines.append("  coverage: 100%")

    lines.append("")
    lines.append(f"module-level cycles: {len(report.module_cycles)}")
    for cycle in report.module_cycles:
        lines.append(f"  FAIL {cycle}")
    lines.append(f"function-level cycles: {len(report.function_cycles)}")
    for cycle in report.function_cycles:
        lines.append(f"  FAIL {cycle}")

    lines.append("")
    lines.append(f"forbidden direction fails: {len(report.forbidden_fail)}")
    for edge in report.forbidden_fail:
        lines.append(f"  FAIL {edge}")
    lines.append(f"forbidden direction warns: {len(report.forbidden_warn)}")
    for edge in report.forbidden_warn:
        lines.append(f"  WARN {edge}")

    lines.append("")
    lines.append(f"direct-table-access violations: {len(report.dta_violations)}")
    for violation in report.dta_violations:
        lines.append(f"  FAIL {violation}")
    lines.append(f"direct-table-access whitelisted: {len(report.dta_whitelisted)}")
    for entry in report.dta_whitelisted:
        lines.append(f"  ok   {entry}")

    if report.failed:
        lines.append("")
        lines.append("RESULT: FAIL")
    else:
        lines.append("")
        lines.append("RESULT: PASS")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="C3 import-graph / lane-coverage / direct-table-access checker",
    )
    parser.add_argument(
        "--app-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app",
        help="control-plane app package directory (default: ../app next to this script)",
    )
    args = parser.parse_args(argv)
    report = analyze(args.app_dir)
    print(_render(report))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
