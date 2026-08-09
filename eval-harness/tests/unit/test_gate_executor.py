from pathlib import Path

from eval_harness.gate_executor import frozen_gate_suite_digest


_FIXED_FILES = (
    "contracts/conformance/test_schemas.py",
    "contracts/conformance/test_wilson.py",
    "contracts/quality-api/openapi.yaml",
    "contracts/fixtures/probes-customer-service.yaml",
    "eval-harness/samples/b1_probe_responses.json",
    "eval-harness/tests/unit/test_gate.py",
    "eval-harness/tests/unit/test_probe_judge.py",
    "eval-harness/tests/unit/test_digests.py",
    "eval-harness/eval_harness/digests.py",
    "eval-harness/eval_harness/probe_judge.py",
    "eval-harness/eval_harness/probe_loader.py",
    "eval-harness/eval_harness/gate.py",
)


def _write_suite_tree(root: Path) -> None:
    for relative in _FIXED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    schema = root / "contracts/schemas/gate-report.schema.json"
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text("{}", encoding="utf-8")
    vector = root / "contracts/wilson/vectors.yaml"
    vector.parent.mkdir(parents=True, exist_ok=True)
    vector.write_text("vectors: []", encoding="utf-8")


def test_frozen_gate_suite_digest_changes_with_executed_resource(tmp_path):
    _write_suite_tree(tmp_path)
    before = frozen_gate_suite_digest(tmp_path)
    assert before.startswith("sha256:")

    target = tmp_path / "contracts/schemas/gate-report.schema.json"
    target.write_text('{"changed":true}', encoding="utf-8")
    after = frozen_gate_suite_digest(tmp_path)
    assert after != before
