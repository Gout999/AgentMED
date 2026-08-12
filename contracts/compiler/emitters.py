"""Deterministic C4 surface emitters: the V5 OpenAPI 3.1 document and the
applications.list TypeScript module.

Both emitters consume only frozen C1 artifacts (the generated operation
manifest and ``contracts/v5/schemas/*.schema.json``) and never touch domain,
API, CLI or Console code. Output is byte-deterministic: no timestamps, no
absolute paths, no environment identity; regenerating must reproduce
identical files (convergence plan C1 determinism).

OpenAPI choices (also documented inside the emitted document):

- ``openapi: 3.1.0`` with only activated operations (the operation manifest
  is the single source of the operation set, so draft/deferred intents can
  never appear).
- request/response/error contracts are referenced with external-file
  ``$ref``s into ``../schemas/<intent>.schema.json#/$defs/...`` (v4
  ``public-api.yaml`` style; the frozen schemas stay the single source of
  truth, no inline duplication). The absolute schema URIs the manifest
  carries are re-based to relative refs that resolve from the generated
  document's location.
- path and query parameters are emitted inline per operation: path
  placeholders resolve to the matching ``common.schema.json`` id definition
  (pattern included), query parameters embed the per-property schema of the
  operation's ``$defs/query`` with all ``$ref``s re-based.
- ``components.securitySchemes`` is intentionally not emitted (C4 decision:
  the V5 auth surface is not cut over, so no bearer scheme is documented).

TypeScript module choices:

- shape mirrors ``console/src/lib/validators.ts``: ``Guard<T>`` type,
  ``interface`` declarations, pattern ``RegExp`` consts, ``exactKeys``
  checks, the revision union handled with an explicit ``revision === 1``
  if/else branch, and an exported ``guards`` map.
- every pattern, enum, required-key order, const value and hash rule is
  extracted from the frozen schemas, so a schema edit changes the generated
  module. Cross-field invariants (workspace envelope binding, exact-binding
  revision chain, component-edge endpoints) mirror the console validators
  and the record chain semantics of ``schema-profiles.yaml``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "contracts/v5/schemas"

# Path-template placeholders -> common.schema.json id definitions. Only the
# five activated by-id GET operations exist today; the frozen schema
# namespace is the authority and this map is the stable projection.
PATH_PARAMETER_ID_DEFS = {
    "application_id": "idApplicationId",
    "environment_id": "idEnvironmentId",
    "component_id": "idComponentId",
    "dependency_edge_id": "idEdgeId",
    "system_version_set_id": "idSystemVersionSetId",
}

# ---------------------------------------------------------------------------
# JSON / JSON-pointer helpers (no external dependency).
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError(f"not a JSON object document: {path}")
    return document


def _json_pointer_get(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError(f"not a JSON pointer: {pointer!r}")
    current = document
    for segment in pointer[1:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list):
            current = current[int(segment)]
        else:
            raise KeyError(f"JSON pointer not found: {pointer}")
    return current


def _split_ref(ref: str) -> tuple[str, str]:
    """Split ``file.schema.json#/pointer`` into (file, pointer)."""
    if "#" in ref:
        file_name, pointer = ref.split("#", 1)
    else:
        file_name, pointer = ref, ""
    if not pointer.startswith("/"):
        raise ValueError(f"unsupported schema ref (pointer must start with /): {ref!r}")
    return file_name, pointer


def _resolve_def(
    schemas: dict[str, dict[str, Any]],
    schemas_dir: Path,
    current_file: str,
    ref: str,
) -> tuple[dict[str, Any], str]:
    """Resolve a relative ``$ref`` to its target def; return (def, def_name).

    Accepts both full refs (``common.schema.json#/$defs/x``) and bare JSON
    pointers (``/$defs/x``, resolved inside ``current_file``).
    """
    if "#" not in ref:
        ref = f"#{ref}"
    file_name, pointer = _split_ref(ref)
    if not file_name:
        file_name = current_file
    if file_name not in schemas:
        path = schemas_dir / file_name
        if not path.is_file():
            raise FileNotFoundError(f"schema ref target missing: {path}")
        schemas[file_name] = _load_json(path)
    target = _json_pointer_get(schemas[file_name], pointer)
    if not isinstance(target, dict):
        raise ValueError(f"schema ref target is not an object: {ref!r}")
    return target, pointer.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# OpenAPI 3.1 emitter
# ---------------------------------------------------------------------------


def _openapi_rel_ref(ref: str, current_file: str) -> str:
    """Re-base a schema-internal ref to one resolvable from generated/.

    ``common.schema.json#/$defs/x`` -> ``../schemas/common.schema.json#/$defs/x``
    ``#/$defs/x`` (same file)     -> ``../schemas/<current_file>#/$defs/x``
    """
    file_name, pointer = _split_ref(ref)
    if not file_name:
        file_name = current_file
    return f"../schemas/{file_name}#{pointer}"


def _openapi_embed(schema: Any, current_file: str) -> Any:
    """Deep-copy a schema fragment, re-basing every ``$ref`` to generated/."""
    if isinstance(schema, dict):
        if "$ref" in schema:
            embedded = {
                key: _openapi_embed(value, current_file)
                for key, value in schema.items()
                if key != "$ref"
            }
            embedded["$ref"] = _openapi_rel_ref(schema["$ref"], current_file)
            return embedded
        return {key: _openapi_embed(value, current_file) for key, value in schema.items()}
    if isinstance(schema, list):
        return [_openapi_embed(item, current_file) for item in schema]
    return schema


def emit_v5_openapi(
    operation_manifest: dict[str, Any], schemas_dir: Path = SCHEMAS_DIR
) -> dict[str, Any]:
    """Build the deterministic OpenAPI 3.1 document for the activated set.

    Only operations present in ``operation_manifest`` (the C1 activated set)
    can generate paths; draft/deferred intents are unreachable by
    construction.
    """
    operations = operation_manifest.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("operation manifest has no activated operations")
    majors = sorted({op.get("contract_major") for op in operations})
    info_version = f"{majors[-1]}.0"

    schemas: dict[str, dict[str, Any]] = {}
    paths: dict[str, dict[str, Any]] = {}
    for operation in operations:
        intent = operation["intent"]
        http = operation["http"]
        path = http["path"]
        method = str(http["method"]).lower()
        operation_id = http.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError(f"activated intent {intent} requires an http operation_id")
        entry: dict[str, Any] = {
            "operationId": operation_id,
            "x-caseloop-intent": intent,
            "x-caseloop-scope": operation["scope"],
            "x-caseloop-wire-status": operation["wire_status"],
            "x-caseloop-delivery-slice": operation["delivery_slice"],
        }

        parameters: list[dict[str, Any]] = []
        for placeholder in re.findall(r"\{([^{}]+)\}", path):
            def_name = PATH_PARAMETER_ID_DEFS.get(placeholder)
            if def_name:
                resolved, _ = _resolve_def(
                    schemas, schemas_dir, "common.schema.json", f"/$defs/{def_name}"
                )
                schema = _openapi_embed(resolved, "common.schema.json")
            else:
                schema = {"type": "string"}
            parameters.append(
                {
                    "name": placeholder,
                    "in": "path",
                    "required": True,
                    "description": f"{placeholder} path parameter of {intent}.",
                    "schema": schema,
                }
            )

        query_parameters = http.get("query_parameters")
        if query_parameters:
            query_def, _ = _resolve_def(
                schemas, schemas_dir, f"{intent}.schema.json", "/$defs/query"
            )
            properties = query_def.get("properties")
            if not isinstance(properties, dict):
                raise ValueError(f"intent {intent} query def has no properties")
            required = set(query_parameters.get("required", []))
            for name in [
                *query_parameters.get("required", []),
                *query_parameters.get("optional", []),
            ]:
                if name not in properties:
                    raise ValueError(
                        f"intent {intent} query parameter {name} has no schema property"
                    )
                parameters.append(
                    {
                        "name": name,
                        "in": "query",
                        "required": name in required,
                        "description": f"{name} query parameter of {intent}.",
                        "schema": _openapi_embed(
                            properties[name], f"{intent}.schema.json"
                        ),
                    }
                )
        if parameters:
            entry["parameters"] = parameters

        request_def, _ = _resolve_def(
            schemas, schemas_dir, f"{intent}.schema.json", "/$defs/request"
        )
        if request_def.get("type") != "null":
            entry["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": f"../schemas/{intent}.schema.json#/$defs/request"
                        }
                    }
                },
            }

        success_status = "201" if operation.get("kind") == "mutation" else "200"
        entry["responses"] = {
            success_status: {
                "description": (
                    f"Successful {intent} response (contract major "
                    f"{operation['contract_major']})."
                ),
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": f"../schemas/{intent}.schema.json#/$defs/response"
                        }
                    }
                },
            },
            "default": {
                "description": (
                    f"Machine-readable error envelope for {intent}; audit failure "
                    "uses null audit_ref."
                ),
                "content": {
                    "application/json": {
                        "schema": {
                            "$ref": f"../schemas/{intent}.schema.json#/$defs/error"
                        }
                    }
                },
            },
        }
        paths.setdefault(path, {})[method] = entry

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "CaseLoop V5 Public API",
            "version": info_version,
            "description": (
                "Generated OpenAPI 3.1 document for the activated V5 wire "
                "surface. Contains only activated operations (FROZEN_R2 / "
                "FROZEN_R2_R3_BOOTSTRAP / FROZEN_R3 intents from the intent "
                "registry); draft and deferred intents never generate paths. "
                "request/response/error contracts are external $refs into "
                "the frozen ../schemas/*.schema.json files. "
                "components.securitySchemes is intentionally not emitted "
                "(C4 decision; the V5 auth surface is not cut over, and a "
                "later wave may add it with the cutover)."
            ),
        },
        "x-caseloop-intent-registry": "../intent-registry.yaml",
        "x-caseloop-operation-manifest": "operation-manifest.json",
        "x-caseloop-generated-by": "caseloop-v5-compiler",
        "servers": [{"url": "/"}],
        "paths": paths,
    }


# ---------------------------------------------------------------------------
# TypeScript applications.list emitter
# ---------------------------------------------------------------------------

# def name -> TS pattern-const name (console validators.ts naming).
_TS_PATTERN_CONST_NAMES = {
    "idWorkspaceId": "WORKSPACE_ID",
    "idProjectId": "PROJECT_ID",
    "idPrincipalId": "PRINCIPAL_ID",
    "idApplicationId": "APPLICATION_ID",
    "idEnvironmentId": "ENVIRONMENT_ID",
    "idComponentId": "COMPONENT_ID",
    "idEdgeId": "EDGE_ID",
    "idRequestId": "REQUEST_ID",
    "idAuthorityReceiptId": "AUTHORITY_RECEIPT_ID",
    "auditRef": "AUDIT_REF",
    "digest": "SHA256_DIGEST",
    "slug": "SLUG",
    "logicalName": "LOGICAL_NAME",
    "applicationListCursor": "CURSOR",
}

# Canonical const emission order; only used consts are emitted.
_TS_CONST_EMIT_ORDER = (
    "SHA256_DIGEST",
    "WORKSPACE_ID",
    "PROJECT_ID",
    "PRINCIPAL_ID",
    "APPLICATION_ID",
    "ENVIRONMENT_ID",
    "COMPONENT_ID",
    "EDGE_ID",
    "REQUEST_ID",
    "AUDIT_REF",
    "CURSOR",
    "SLUG",
    "LOGICAL_NAME",
)

# def name -> TS record type name.
_TS_RECORD_TYPE_NAMES = {
    "recordEnvelope": "Envelope",
    "exactApplicationBinding": "ExactBinding",
    "exactSystemComponentBinding": "ExactBinding",
    "applicationRecord": "ApplicationRecord",
    "environmentRecord": "EnvironmentRecord",
    "componentRecord": "ComponentRecord",
    "dependencyEdgeRecord": "EdgeRecord",
    "applicationListItem": "ApplicationCatalogItem",
}

# def name -> TS guard name.
_TS_GUARD_NAMES = {
    "recordEnvelope": "envelope",
    "exactApplicationBinding": "exactBinding",
    "exactSystemComponentBinding": "exactBinding",
    "applicationRecord": "applicationRecord",
    "environmentRecord": "environmentRecord",
    "componentRecord": "componentRecord",
    "dependencyEdgeRecord": "edgeRecord",
    "applicationListItem": "applicationCatalogItem",
}

# format: date-time pattern carried over from console validators.ts; the
# frozen schemas express this as a format keyword, not a regex.
_TS_AWARE_DATETIME_SOURCE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"


def _ts_const_name(def_name: str) -> str:
    if def_name in _TS_PATTERN_CONST_NAMES:
        return _TS_PATTERN_CONST_NAMES[def_name]
    return re.sub(r"(?<!^)(?=[A-Z])", "_", def_name).upper()


def _ts_union_alias(def_name: str) -> str:
    return def_name[0].upper() + def_name[1:]


class _TsContext:
    """Collects emitted artifacts so the module is assembled deterministically."""

    def __init__(self, schemas: dict[str, dict[str, Any]], schemas_dir: Path) -> None:
        self.schemas = schemas
        self.schemas_dir = schemas_dir
        self.pattern_consts: dict[str, str] = {}  # name -> JS pattern source
        self.used_unions: set[str] = set()

    def register_pattern_const(self, def_name: str, pattern: str) -> str:
        const_name = _ts_const_name(def_name)
        self.pattern_consts.setdefault(const_name, pattern)
        return const_name

    def use_union(self, def_name: str) -> str:
        self.used_unions.add(def_name)
        return _ts_union_alias(def_name)


def _ts_regex_literal(pattern: str) -> str:
    if "\n" in pattern:
        raise ValueError(f"pattern cannot be emitted as a regex literal: {pattern!r}")
    return "/" + pattern.replace("/", r"\/") + "/"


def _ts_quote(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ts_enum_literal(enum: list[Any]) -> str:
    return "[" + ", ".join(_ts_quote(item) for item in enum) + "]"


def _ts_positive_clauses(
    ctx: _TsContext, current_file: str, prop: str, schema: dict[str, Any]
) -> list[str]:
    """Positive guard clauses for one property (schema-driven)."""
    value = f"value.{prop}"
    schemas_dir = ctx.schemas_dir

    if "const" in schema:
        return [f"{value} === {_ts_quote(schema['const'])}"]

    if "$ref" in schema:
        resolved, def_name = _resolve_def(
            ctx.schemas, schemas_dir, current_file, schema["$ref"]
        )
        if "const" in resolved:
            return [f"{value} === {_ts_quote(resolved['const'])}"]
        if "enum" in resolved:
            return [
                f"{_ts_enum_literal(resolved['enum'])}.includes(String({value}))"
            ]
        kind = resolved.get("type")
        if kind == "string":
            if "pattern" in resolved:
                const_name = ctx.register_pattern_const(def_name, resolved["pattern"])
                return [f"typeof {value} === \"string\" && {const_name}.test({value})"]
            return [f"typeof {value} === \"string\""]
        if kind == "integer":
            clauses = [f"integer({value})"]
            if "minimum" in resolved:
                clauses.append(f"{value} >= {resolved['minimum']}")
            if "maximum" in resolved:
                clauses.append(f"{value} <= {resolved['maximum']}")
            return clauses
        if kind == "boolean":
            return [f"typeof {value} === \"boolean\""]
        if kind == "null":
            return [f"{value} === null"]
        if kind == "array":
            items = resolved.get("items")
            if isinstance(items, dict) and "$ref" in items:
                return _ts_array_clauses(ctx, current_file, value, items)
            return [f"Array.isArray({value})"]
        if kind == "object":
            return [_ts_object_ref_clause(ctx, current_file, value, def_name)]
        raise ValueError(f"unsupported property schema type {kind!r} for {prop}")

    if "anyOf" in schema:
        members = schema["anyOf"]
        null_members = [m for m in members if m.get("type") == "null"]
        other_members = [m for m in members if m.get("type") != "null"]
        if len(null_members) == 1 and len(other_members) == 1:
            inner = " && ".join(
                _ts_positive_clauses(ctx, current_file, prop, other_members[0])
            )
            return [f"{value} === null || ({inner})"]
        raise ValueError(f"unsupported anyOf for {prop}: {schema['anyOf']!r}")

    kind = schema.get("type")
    if kind == "string":
        clauses = [f"typeof {value} === \"string\""]
        if "minLength" in schema:
            clauses.append(f"{value}.length >= {schema['minLength']}")
        if "maxLength" in schema:
            clauses.append(f"{value}.length <= {schema['maxLength']}")
        return clauses
    if kind == "integer":
        clauses = [f"integer({value})"]
        if "minimum" in schema:
            clauses.append(f"{value} >= {schema['minimum']}")
        if "maximum" in schema:
            clauses.append(f"{value} <= {schema['maximum']}")
        return clauses
    if kind == "boolean":
        return [f"typeof {value} === \"boolean\""]
    if kind == "null":
        return [f"{value} === null"]
    if kind == "array":
        items = schema.get("items")
        if isinstance(items, dict) and "$ref" in items:
            return _ts_array_clauses(ctx, current_file, value, items)
        return [f"Array.isArray({value})"]
    raise ValueError(f"unsupported inline property schema for {prop}: {schema!r}")


def _ts_array_clauses(
    ctx: _TsContext, current_file: str, value: str, items: dict[str, Any]
) -> list[str]:
    """Guard clauses for an array whose items are a ``$ref``."""
    item_def, item_name = _resolve_def(
        ctx.schemas, ctx.schemas_dir, current_file, items["$ref"]
    )
    if item_def.get("type") == "string" and "pattern" in item_def:
        const_name = ctx.register_pattern_const(item_name, item_def["pattern"])
        return [f"uniqueStrings({value}, {const_name})"]
    if item_def.get("type") == "object":
        return [
            f"Array.isArray({value}) && {value}.every({_TS_GUARD_NAMES[item_name]})"
        ]
    return [f"Array.isArray({value})"]


def _ts_object_ref_clause(
    ctx: _TsContext, current_file: str, value: str, def_name: str
) -> str:
    """Guard clause for a ``$ref`` to an object def (envelope/binding/record)."""
    if def_name == "recordEnvelope":
        return f"envelope({value})"
    if def_name in ("exactApplicationBinding", "exactSystemComponentBinding"):
        resolved, _ = _resolve_def(
            ctx.schemas, ctx.schemas_dir, current_file, f"/$defs/{def_name}"
        )
        kind_const = resolved["properties"]["kind"]["const"]
        id_ref = resolved["properties"]["id"]["$ref"]
        id_def, id_def_name = _resolve_def(
            ctx.schemas, ctx.schemas_dir, current_file, id_ref
        )
        const_name = ctx.register_pattern_const(id_def_name, id_def["pattern"])
        return f"exactBinding({value}, {_ts_quote(kind_const)}, {const_name})"
    return f"{_TS_GUARD_NAMES[def_name]}({value})"


def _ts_property_type(
    ctx: _TsContext, current_file: str, schema: dict[str, Any]
) -> str:
    """TS field type for one property schema (schema-driven)."""
    schemas_dir = ctx.schemas_dir

    if "const" in schema:
        return _ts_quote(schema["const"])

    if "$ref" in schema:
        resolved, def_name = _resolve_def(
            ctx.schemas, schemas_dir, current_file, schema["$ref"]
        )
        if "const" in resolved:
            return _ts_quote(resolved["const"])
        if "enum" in resolved:
            return ctx.use_union(def_name)
        kind = resolved.get("type")
        if kind == "string":
            return "string"
        if kind == "integer":
            return "number"
        if kind == "boolean":
            return "boolean"
        if kind == "null":
            return "null"
        if kind == "array":
            items = resolved.get("items")
            if isinstance(items, dict) and "$ref" in items:
                item_def, item_name = _resolve_def(
                    ctx.schemas, schemas_dir, current_file, items["$ref"]
                )
                if "enum" in item_def:
                    return f"{ctx.use_union(item_name)}[]"
                return f"{_ts_property_type(ctx, current_file, items)}[]"
            return "unknown[]"
        if kind == "object":
            return _TS_RECORD_TYPE_NAMES.get(def_name, "Record<string, unknown>")
        raise ValueError(f"unsupported ref type for TS property: {resolved!r}")

    if "anyOf" in schema:
        member_types = [
            _ts_property_type(ctx, current_file, member) for member in schema["anyOf"]
        ]
        return " | ".join(member_types)

    kind = schema.get("type")
    if kind == "string":
        return "string"
    if kind == "integer":
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        items = schema.get("items", {})
        if isinstance(items, dict) and "$ref" in items:
            item_def, item_name = _resolve_def(
                ctx.schemas, schemas_dir, current_file, items["$ref"]
            )
            if "enum" in item_def:
                return f"{ctx.use_union(item_name)}[]"
            return f"{_ts_property_type(ctx, current_file, items)}[]"
        return "unknown[]"
    raise ValueError(f"unsupported TS property schema: {schema!r}")


def _ts_revision_metadata(
    ctx: _TsContext, current_file: str, record_def: dict[str, Any]
) -> dict[str, Any]:
    """Extract revision-union metadata from a record def (fail closed)."""
    properties = record_def["properties"]
    or_null = [name for name in properties if name.endswith("_or_null")]
    if len(or_null) != 1:
        raise ValueError(f"expected exactly one *_or_null property in {current_file}")
    binding = None
    for name, prop in properties.items():
        if isinstance(prop, dict) and isinstance(prop.get("$ref"), str):
            pointer = _split_ref(prop["$ref"])[1]
            if pointer.rsplit("/", 1)[-1] in (
                "exactApplicationBinding",
                "exactSystemComponentBinding",
            ):
                binding = name
    if binding is None:
        raise ValueError(f"no exact binding property found in {current_file}")
    binding_def_name = _split_ref(properties[binding]["$ref"])[1].rsplit("/", 1)[-1]
    binding_def, _ = _resolve_def(
        ctx.schemas, ctx.schemas_dir, current_file, f"/$defs/{binding_def_name}"
    )
    kind_const = binding_def["properties"]["kind"]["const"]
    id_ref = binding_def["properties"]["id"]["$ref"]
    id_def, id_def_name = _resolve_def(
        ctx.schemas, ctx.schemas_dir, current_file, id_ref
    )
    id_const = ctx.register_pattern_const(id_def_name, id_def["pattern"])
    id_property = next(
        name
        for name, prop in properties.items()
        if isinstance(prop, dict)
        and isinstance(prop.get("$ref"), str)
        and _split_ref(prop["$ref"])[1].rsplit("/", 1)[-1] == id_def_name
    )
    lifecycle_const = None
    for rule in record_def.get("allOf", []):
        rule_props = rule.get("then", {}).get("properties", {})
        lifecycle_state = rule_props.get("lifecycle_state")
        if isinstance(lifecycle_state, dict) and "const" in lifecycle_state:
            lifecycle_const = lifecycle_state["const"]
    if lifecycle_const is None:
        raise ValueError(f"no lifecycle_state const rule in {current_file}")
    return {
        "or_null": or_null[0],
        "binding": binding,
        "binding_def": binding_def_name,
        "kind_const": kind_const,
        "id_const": id_const,
        "id_property": id_property,
        "lifecycle_const": lifecycle_const,
    }


def _ts_interface(
    ctx: _TsContext, name: str, current_file: str, record_def: dict[str, Any]
) -> list[str]:
    lines = [f"export interface {name} {{"]
    for prop_name, prop in record_def["properties"].items():
        lines.append(f"  {prop_name}: {_ts_property_type(ctx, current_file, prop)};")
    lines.append("}")
    return lines


def _ts_conjunction_guard(
    ctx: _TsContext,
    current_file: str,
    type_name: str,
    guard_name: str,
    record_def: dict[str, Any],
    extra_clauses: list[str] | None = None,
) -> list[str]:
    required = record_def["required"]
    clauses: list[str] = []
    for name in required:
        clauses.extend(_ts_positive_clauses(ctx, current_file, name, record_def["properties"][name]))
    for clause in extra_clauses or []:
        clauses.append(clause)
    lines = [
        f"const {guard_name}: Guard<{type_name}> = (value): value is {type_name} => record(value)",
        f"  && exactKeys(value, [{', '.join(_ts_quote(key) for key in required)}])",
    ]
    for clause in clauses:
        lines.append(f"  && {clause}")
    lines[-1] += ";"
    return lines


def _ts_revision_guard(
    ctx: _TsContext,
    current_file: str,
    type_name: str,
    guard_name: str,
    record_def: dict[str, Any],
    revision: dict[str, Any],
) -> list[str]:
    properties = record_def["properties"]
    base_keys = record_def["required"]
    check_props = [
        name for name in properties if name not in (revision["or_null"], revision["binding"])
    ]
    negated = [
        _ts_negated(clause)
        for name in check_props
        for clause in _ts_positive_clauses(ctx, current_file, name, properties[name])
    ]
    negated.append(_ts_negated("value.workspace_id === value.record_envelope.workspace_id"))
    lines = [
        f"const {guard_name}: Guard<{type_name}> = (value): value is {type_name} => {{",
        "  if (!record(value) || !envelope(value.record_envelope)) return false;",
        "  const revision = value.record_envelope.revision;",
        f"  const revisionKeys = revision === 1 ? [{_ts_quote(revision['or_null'])}] : [{_ts_quote(revision['binding'])}];",
        "  if (!exactKeys(value, ["
        + ", ".join(_ts_quote(key) for key in base_keys)
        + ", ...revisionKeys])",
    ]
    for clause in negated:
        lines.append(f"    || {clause}")
    lines.append(") return false;")
    lines.append("  if (revision === 1) {")
    lines.append(
        f"    return value.lifecycle_state === {_ts_quote(revision['lifecycle_const'])}"
    )
    lines.append(f"      && value.{revision['or_null']} === null;")
    lines.append("  }")
    lines.append(
        f"  return exactBinding(value.{revision['binding']}, "
        f"{_ts_quote(revision['kind_const'])}, {revision['id_const']})"
    )
    lines.append(f"    && value.{revision['binding']}.id === value.{revision['id_property']}")
    lines.append("    && value." + revision["binding"] + ".revision === revision - 1;")
    lines.append("};")
    return lines


def _ts_negated(expr: str) -> str:
    return f"!({expr})"


def emit_ts_application_list(schemas_dir: Path = SCHEMAS_DIR) -> str:
    """Generate the applications.list TypeScript types + guards module."""
    schemas: dict[str, dict[str, Any]] = {}
    for file_name in (
        "applications.list.schema.json",
        "records.schema.json",
        "common.schema.json",
    ):
        path = schemas_dir / file_name
        if not path.is_file():
            raise FileNotFoundError(f"TS source schema missing: {path}")
        schemas[file_name] = _load_json(path)
    ctx = _TsContext(schemas, schemas_dir)

    common_defs = schemas["common.schema.json"]["$defs"]
    records_defs = schemas["records.schema.json"]["$defs"]
    list_defs = schemas["applications.list.schema.json"]["$defs"]

    envelope_def = common_defs["recordEnvelope"]
    app_def = records_defs["applicationRecord"]
    env_def = records_defs["environmentRecord"]
    component_def = records_defs["componentRecord"]
    edge_def = records_defs["dependencyEdgeRecord"]
    item_def = records_defs["applicationListItem"]
    list_response_def = list_defs["response"]

    app_revision = _ts_revision_metadata(ctx, "records.schema.json", app_def)
    component_revision = _ts_revision_metadata(
        ctx, "records.schema.json", component_def
    )

    exact_kind_consts = [
        records_defs[name]["properties"]["kind"]["const"]
        for name in ("exactApplicationBinding", "exactSystemComponentBinding")
    ]
    binding_keys = _ts_quote(
        records_defs["exactApplicationBinding"]["required"]
    )

    # ------------------------------------------------------------- interfaces
    interfaces: list[str] = []
    interfaces.append("// ---------- types ----------")
    for name, record_def, current_file in (
        ("Envelope", envelope_def, "common.schema.json"),
        ("ApplicationRecord", app_def, "records.schema.json"),
        ("EnvironmentRecord", env_def, "records.schema.json"),
        ("ComponentRecord", component_def, "records.schema.json"),
        ("EdgeRecord", edge_def, "records.schema.json"),
        ("ApplicationCatalogItem", item_def, "records.schema.json"),
        ("ApplicationCatalogList", list_response_def, "applications.list.schema.json"),
    ):
        interfaces.append("")
        interfaces.extend(_ts_interface(ctx, name, current_file, record_def))
    exact_binding_interface = [
        "export interface ExactBinding {",
        f"  kind: {' | '.join(_ts_quote(kind) for kind in exact_kind_consts)};",
        "  id: string;",
        "  revision: number;",
        "  digest: string;",
        "}",
    ]
    # union aliases are emitted first, after the interfaces above populated
    # the referenced-enum set (deterministic: sorted by alias name).
    union_lines = [
        f"export type {_ts_union_alias(name)} = "
        + " | ".join(_ts_quote(value) for value in common_defs[name]["enum"])
        + ";"
        for name in sorted(ctx.used_unions)
    ]

    # ---------------------------------------------------------------- guards
    guards: list[str] = []
    guards.append("// ---------- guards ----------")
    envelope_clauses: list[str] = []
    for name in envelope_def["required"]:
        if name == "hash_rule":
            envelope_clauses.append("value.hash_rule === RECORD_HASH_RULE")
        elif name == "recorded_at":
            envelope_clauses.append(
                'typeof value.recorded_at === "string" && '
                "AWARE_DATETIME.test(value.recorded_at)"
            )
        else:
            envelope_clauses.extend(
                _ts_positive_clauses(ctx, "common.schema.json", name, envelope_def["properties"][name])
            )
    guards.append(
        "const envelope: Guard<Envelope> = (value): value is Envelope => record(value)"
    )
    guards.append(
        "  && exactKeys(value, ["
        + ", ".join(_ts_quote(key) for key in envelope_def["required"])
        + "])"
    )
    for clause in envelope_clauses:
        guards.append(f"  && {clause}")
    guards[-1] += ";"
    guards.append("")
    guards.extend(
        [
            "function exactBinding(",
            '  value: unknown, kind: "AI_APPLICATION" | "SYSTEM_COMPONENT",',
            "  idPattern: RegExp,",
            "): value is Record<string, unknown> {",
            "  return record(value)",
            f"    && exactKeys(value, {binding_keys})",
            "    && value.kind === kind",
            '    && typeof value.id === "string" && idPattern.test(value.id)',
            "    && integer(value.revision) && value.revision >= 1",
            '    && typeof value.digest === "string" && SHA256_DIGEST.test(value.digest)',
            "}",
        ]
    )
    guards.append("")
    guards.extend(
        [
            "const isExactBinding: Guard<ExactBinding> = (value): value is ExactBinding => exactBinding(value, \"AI_APPLICATION\", APPLICATION_ID)",
            "  || exactBinding(value, \"SYSTEM_COMPONENT\", COMPONENT_ID);",
        ]
    )
    guards.append("")
    guards.extend(
        _ts_revision_guard(
            ctx,
            "records.schema.json",
            "ApplicationRecord",
            "applicationRecord",
            app_def,
            app_revision,
        )
    )
    guards.append("")
    guards.extend(
        _ts_conjunction_guard(
            ctx,
            "records.schema.json",
            "EnvironmentRecord",
            "environmentRecord",
            env_def,
            extra_clauses=["value.workspace_id === value.record_envelope.workspace_id"],
        )
    )
    guards.append("")
    guards.extend(
        _ts_revision_guard(
            ctx,
            "records.schema.json",
            "ComponentRecord",
            "componentRecord",
            component_def,
            component_revision,
        )
    )
    guards.append("")
    guards.extend(
        _ts_conjunction_guard(
            ctx,
            "records.schema.json",
            "EdgeRecord",
            "edgeRecord",
            edge_def,
            extra_clauses=["value.from_component_id !== value.to_component_id"],
        )
    )

    item_props = list(item_def["properties"])
    app_prop = item_props[0]
    array_props = item_props[1:]
    array_clauses = []
    for array_name in array_props:
        items_ref = item_def["properties"][array_name]["items"]["$ref"]
        item_def_name = _split_ref(items_ref)[1].rsplit("/", 1)[-1]
        array_clauses.append(
            f"!Array.isArray(value.{array_name}) || "
            f"!value.{array_name}.every({_TS_GUARD_NAMES[item_def_name]})"
        )
    guards.append("")
    guards.extend(
        [
            f"const applicationCatalogItem: Guard<ApplicationCatalogItem> = (value): value is ApplicationCatalogItem => {{",
            "  if (!record(value)",
            f"    || !exactKeys(value, [{', '.join(_ts_quote(name) for name in item_props)}])",
            f"    || !applicationRecord(value.{app_prop})",
        ]
    )
    for clause in array_clauses:
        guards.append(f"    || {clause}")
    guards.append(") return false;")
    guards.append(f"  const workspaceId = value.{app_prop}.workspace_id;")
    guards.append(f"  const applicationId = value.{app_prop}.application_id;")
    guards.append(
        f"  const componentIds = new Set(value.{array_props[1]}.map("
        f"(component) => component.{component_revision['id_property']}));"
    )
    guards.append(
        "  const records = [...value."
        + ", ...value.".join(array_props)
        + "];"
    )
    guards.append(
        "  return records.every((item) => item.workspace_id === workspaceId "
        "&& item.application_id === applicationId)"
    )
    guards.append(
        f"    && value.{array_props[2]}.every((edge) => ("
    )
    guards.append(
        "      componentIds.has(edge.from_component_id) "
        "&& componentIds.has(edge.to_component_id)"
    )
    guards.append("    ));")
    guards.append("};")

    list_required = list_response_def["required"]
    list_props = list_response_def["properties"]
    list_negated = [
        _ts_negated(clause)
        for name in list_required
        if name not in ("items", "next_cursor")
        for clause in _ts_positive_clauses(
            ctx, "applications.list.schema.json", name, list_props[name]
        )
    ]
    list_negated.append(
        "!Array.isArray(value.items) || !value.items.every(applicationCatalogItem)"
    )
    next_cursor_clauses = _ts_positive_clauses(
        ctx, "applications.list.schema.json", "next_cursor", list_props["next_cursor"]
    )
    list_negated.append(_ts_negated(" && ".join(next_cursor_clauses)))
    guards.append("")
    guards.extend(
        [
            "const applicationCatalogList: Guard<ApplicationCatalogList> = (value): value is ApplicationCatalogList => {",
            "  if (!record(value)",
            f"    || !exactKeys(value, [{', '.join(_ts_quote(name) for name in list_required)}])",
        ]
    )
    for clause in list_negated:
        guards.append(f"    || {clause}")
    guards.append(") return false;")
    guards.append(
        "  return value.items.every((item) => item.application.workspace_id "
        "=== value.workspace_id)"
    )
    guards.append(
        "    && new Set(value.items.map((item) => item.application.application_id)).size "
        "=== value.items.length;"
    )
    guards.append("};")

    # -------------------------------------------------------------- assembly
    used_const_names = set(ctx.pattern_consts)
    ordered_consts = [
        name for name in _TS_CONST_EMIT_ORDER if name in used_const_names
    ]
    ordered_consts.extend(
        name for name in ctx.pattern_consts if name not in ordered_consts
    )

    parts: list[str] = [
        "// Generated by the CaseLoop V5 compiler (contracts/compiler/emitters.py).",
        "// Deterministic output: do not edit by hand; regenerate with",
        "//     python -m compiler emit",
        "// Sources (frozen C1 contracts):",
        "//   contracts/v5/schemas/applications.list.schema.json",
        "//   contracts/v5/schemas/records.schema.json",
        "//   contracts/v5/schemas/common.schema.json",
        "// Shape mirrors console/src/lib/validators.ts.",
        "",
        "export type Guard<T> = (value: unknown) => value is T;",
        "",
    ]
    for const_name in ordered_consts:
        parts.append(
            f"const {const_name} = {_ts_regex_literal(ctx.pattern_consts[const_name])};"
        )
    parts.append("")
    parts.append(
        "const RECORD_HASH_RULE = "
        + _ts_quote(envelope_def["properties"]["hash_rule"]["const"])
        + ";"
    )
    parts.append("")
    parts.append(f"const AWARE_DATETIME = {_ts_regex_literal(_TS_AWARE_DATETIME_SOURCE)};")
    parts.extend(
        [
            "",
            "function record(value: unknown): value is Record<string, unknown> {",
            '  return typeof value === "object" && value !== null && !Array.isArray(value);',
            "}",
            "",
            "function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {",
            "  const actual = Object.keys(value).sort();",
            "  const expected = [...keys].sort();",
            "  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);",
            "}",
            "",
            "function uniqueStrings(value: unknown, pattern?: RegExp): value is string[] {",
            "  return Array.isArray(value)",
            "    && value.length > 0",
            '    && value.every((item) => typeof item === "string" && (pattern === undefined || pattern.test(item)))',
            "    && new Set(value).size === value.length;",
            "}",
            "",
            "function integer(value: unknown): value is number {",
            "  return Number.isInteger(value);",
            "}",
            "",
        ]
    )
    parts.extend(
        [
            "",
            "// ---------- types ----------",
        ]
    )
    if union_lines:
        parts.extend(union_lines)
        parts.append("")
    parts.extend(exact_binding_interface)
    # interfaces[0] is the "// ---------- types ----------" marker; the
    # record interfaces follow it in fixed order.
    parts.extend(interfaces[1:])
    parts.append("")
    parts.extend(guards)
    parts.extend(
        [
            "",
            "export const guards = {",
            "  applicationCatalogList,",
            "  applicationCatalogItem,",
            "  applicationRecord,",
            "  environmentRecord,",
            "  componentRecord,",
            "  edgeRecord,",
            "  envelope,",
            "  exactBinding: isExactBinding,",
            "};",
            "",
        ]
    )
    return "\n".join(parts)
