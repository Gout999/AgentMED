"""Machine checks for v4 contract vocabulary reflected in authority docs."""
from __future__ import annotations

import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PROGRESSIVE = REPO / "docs" / "plans" / "v4-progressive-delivery.md"
PLAN = REPO / "docs" / "plan-v4.md"
RUNTIME_ADR = REPO / "docs" / "decisions" / "D-009-v4-runtime-causality.md"
CAPABILITY_SCHEMA = REPO / "contracts" / "v4" / "schemas" / "capability-lease.schema.json"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_progressive_plan_uses_canonical_facets_and_metadata_categories() -> None:
    text = _text(PROGRESSIVE)
    assert "contract+replay" not in text
    assert "contract+client-live" not in text
    assert "facet：`production-rehearsal`" not in text
    assert "evidence category：`client-live`" in text
    assert "`production-rehearsal` 只写入 evidence category/metadata" in text


def test_stage_one_uses_the_versioned_v4_openapi_contract() -> None:
    text = _text(PROGRESSIVE)
    stage_one = text.split("## Stage 1", 1)[1].split("## Stage 2", 1)[0]
    assert "contracts/v4/openapi/public-api.yaml" in stage_one
    assert "contracts/public-api/openapi.yaml" not in text
    assert "Manual HTTP/CLI Signal intake" in stage_one
    assert "Manual HTTP/CLI/MCP Signal intake" not in stage_one


def test_runtime_adr_uses_formal_capability_and_model_receipt_names() -> None:
    schema = json.loads(CAPABILITY_SCHEMA.read_text(encoding="utf-8"))
    assert schema["properties"]["grant_kind"]["enum"] == [
        "DISPATCH_CLAIM",
        "ATTEMPT_RUNTIME",
        "ACTION_EXECUTION",
    ]
    text = _text(RUNTIME_ADR)
    assert "RuntimeGrant" not in text
    assert "CapabilityLease(grant_kind=DISPATCH_CLAIM)" in text
    assert "CapabilityLease(grant_kind=ATTEMPT_RUNTIME)" in text
    assert "model_resolution_receipt_digest" in text
    assert "model_call_receipt_digest" in text
    assert "model_receipt_digest" not in text


def test_plan_declares_signal_submit_canonical_and_mcp_stage_six() -> None:
    text = _text(PLAN)
    assert "`caseloop report` 是 `caseloop signal submit` 的短 alias" in text
    stage_one = text.split("### Stage 1：", 1)[1].split("### Stage 2：", 1)[0]
    assert "Public MCP/A2A 留到 Stage 6" in stage_one
    assert "stdio Public MCP" not in stage_one
