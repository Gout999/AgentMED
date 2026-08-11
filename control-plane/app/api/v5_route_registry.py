"""C4 generated-transport cutover: public_v5 route table vs C1 manifest gate.

``check_registered_v5_routes`` compares a router's live ``APIRoute`` table
against the C1 activated-operation manifest
(``contracts/v5/generated/operation-manifest.json``, loaded through
``app.services.v5_capabilities.load_v5_operation_manifest``).  Every
registered v5 route's (method, path, operation_id) must exist in the
manifest's http entries and no registered route may be absent from it; any
disagreement raises ``RouteManifestMismatchError`` (fail-closed, with
machine-readable missing/extra detail sets).  This keeps discovery, help,
OpenAPI and capability surfaces from claiming routes the manifest does not
activate — and from hiding routes the manifest does activate.

Fallback semantics (C4 "explicit per-surface fallback"): registration facts
stay legacy authority — the ``@router.*`` decorators in ``public_v5`` remain
the truth for what actually serves.  ``install_route_manifest_check`` is the
import-time hook: it runs the fail-closed gate and, when the gate rejects
(mismatch or manifest unavailability), prints a loud warning and lets the
legacy registration keep serving so legacy/V3/V4 paths are never degraded by
discovery-side drift.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

from fastapi import APIRouter
from fastapi.routing import APIRoute

from app.services.v5_capabilities import (
    V5CapabilitiesManifestError,
    V5OperationManifest,
    load_v5_operation_manifest,
)

# Route identity key: (method, path, operation_id).
_RouteKey = tuple[str, str, str]

# Starlette auto-methods that are not real registered surfaces.
_DERIVED_METHODS = frozenset({"HEAD", "OPTIONS"})


class RouteManifestMismatchError(RuntimeError):
    """Fail-closed: registered v5 routes disagree with the C1 manifest.

    ``missing`` lists manifest http routes not present in the router table;
    ``extra`` lists registered routes absent from the manifest.  Both are
    sorted (method, path, operation_id) triples.
    """

    def __init__(
        self,
        *,
        missing: Sequence[_RouteKey] = (),
        extra: Sequence[_RouteKey] = (),
    ) -> None:
        self.missing: tuple[_RouteKey, ...] = tuple(sorted(missing))
        self.extra: tuple[_RouteKey, ...] = tuple(sorted(extra))
        self.details: dict[str, object] = {
            "missing": [list(key) for key in self.missing],
            "extra": [list(key) for key in self.extra],
        }
        super().__init__(
            "v5.route_registry.mismatch: "
            f"{len(self.missing)} manifest http route(s) not registered"
            f" {[list(key) for key in self.missing]}; "
            f"{len(self.extra)} registered route(s) not in manifest"
            f" {[list(key) for key in self.extra]}"
        )


def _registered_v5_routes(router: APIRouter) -> set[_RouteKey]:
    registered: set[_RouteKey] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method in _DERIVED_METHODS:
                continue
            registered.add((method, route.path, route.operation_id))
    return registered


def _manifest_v5_routes(manifest: V5OperationManifest) -> set[_RouteKey]:
    return {
        (entry.method.upper(), entry.path, entry.operation_id)
        for entry in manifest.http_entries
    }


def check_registered_v5_routes(
    router: APIRouter,
    *,
    manifest: V5OperationManifest | None = None,
    manifest_path: str | Path | None = None,
) -> None:
    """Assert the router table is exactly the manifest http surface.

    Raises ``RouteManifestMismatchError`` on any missing/extra route; returns
    ``None`` when the tables match exactly.

    ``manifest`` takes precedence when both it and ``manifest_path`` are
    given.  ``manifest_path`` mirrors ``load_v5_operation_manifest``: the
    repository root that must contain ``generated/operation-manifest.json``.
    """
    if manifest is None:
        manifest = load_v5_operation_manifest(explicit=manifest_path)
    registered = _registered_v5_routes(router)
    expected = _manifest_v5_routes(manifest)
    missing = expected - registered
    extra = registered - expected
    if missing or extra:
        raise RouteManifestMismatchError(missing=missing, extra=extra)


def install_route_manifest_check(router: APIRouter) -> None:
    """Import-time gate hook with explicit legacy fallback.

    Registration facts remain legacy authority: the decorators already
    installed the routes and they keep serving.  The gate is discovery-side
    fail-closed; on rejection (mismatch or manifest unavailability) it prints
    a clear error to stderr and does not raise, so the legacy table serves on
    regardless of discovery drift.
    """
    try:
        check_registered_v5_routes(router)
    except (RouteManifestMismatchError, V5CapabilitiesManifestError) as exc:
        print(
            "WARNING: v5 route registry gate FAILED; serving legacy "
            f"registration: {exc}",
            file=sys.stderr,
        )


__all__ = [
    "RouteManifestMismatchError",
    "check_registered_v5_routes",
    "install_route_manifest_check",
]
