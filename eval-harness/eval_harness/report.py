"""报告构建与 schema 校验：evidence-bundle + attribution-report（contracts/schemas 权威）。

一切「最终状态」断言必须可机器复核：报告必须通过对应 schema.json 校验。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
from jsonschema import Draft202012Validator

from .digests import canonical_json_bytes, digest_of_bytes

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / ".." / "contracts" / "schemas"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """生成契约 ID：`<prefix>_` + 20 位 hex（匹配 ^(prefix)_[0-9A-Za-z]{8,64}$）。"""
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def load_schema(name: str) -> dict:
    path = (SCHEMAS_DIR / name).resolve()
    if not path.exists():
        raise FileNotFoundError(f"契约 schema 不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_report(report: dict, schema_name: str) -> list[str]:
    """校验报告；返回错误列表（空 = 通过）。"""
    schema = load_schema(schema_name)
    cls = Draft202012Validator(schema)
    errors = sorted(cls.iter_errors(report), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors]


def assert_schema_valid(report: dict, schema_name: str) -> None:
    errors = validate_report(report, schema_name)
    if errors:
        raise AssertionError(f"{schema_name} 校验失败:\n" + "\n".join(errors))


def report_digest(report: dict) -> str:
    """报告自身 digest（用于 evidence_bundle_ref / 门禁 report_hash 类引用）。"""
    return digest_of_bytes(canonical_json_bytes(report))
