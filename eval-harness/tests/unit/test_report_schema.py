"""报告 schema 契约自洽：样例报告必须通过对应 schema（与 conformance test_schemas 同口径）。"""
import json

import pytest

from eval_harness.report import load_schema, validate_report

SAMPLES = [
    ("sample-attribution-report.json", "attribution-report.schema.json"),
    ("sample-evidence-bundle.json", "evidence-bundle.schema.json"),
    ("sample-gate-report.json", "gate-report.schema.json"),
]


@pytest.mark.parametrize("sample_name,schema_name", SAMPLES)
def test_sample_report_validates(sample_name, schema_name, contracts_dir):
    report = json.loads((contracts_dir / "fixtures" / "samples" / sample_name).read_text(encoding="utf-8"))
    assert validate_report(report, schema_name) == [], f"{sample_name} 未通过 {schema_name}"


def test_attribution_report_rejects_vague_confidence(contracts_dir):
    """杜绝「置信≥0.8」式未定义指标：schema 无此字段，extra 字段必须被拒。"""
    sample = json.loads((contracts_dir / "fixtures" / "samples" / "sample-attribution-report.json").read_text(encoding="utf-8"))
    sample["confidence_score"] = 0.8  # 非 schema 字段
    errs = validate_report(sample, "attribution-report.schema.json")
    assert errs, "附加未定义指标字段应被 schema 拒绝"


def test_attribution_verdict_enum():
    schema = load_schema("attribution-report.schema.json")
    verdict = schema["properties"]["verdict"]["properties"]
    assert verdict["decision"]["enum"] == ["ATTRIBUTED", "INCONCLUSIVE", "CONFOUNDED"]
    assert "confidence" not in verdict  # 废除置信指标
