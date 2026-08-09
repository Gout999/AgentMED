#!/usr/bin/env python
"""CLI：对显式指定的候选 VersionSet 跑真实双轨门禁。

用法：
    .venv/bin/python scripts/run_gate.py --versionset-id vs_... [--out-dir evidence/gate-live]

本命令只使用 Quality API 读接口，不注入/复位故障，也不追随 active 指针。候选内容、
revision 与每条 provider 响应都会绑定在证据中；门禁非 passed 时进程返回非零。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_harness.client import QualityAPIClient
from eval_harness.config import get_settings
from eval_harness.gate import GateCandidate, GateRunner, LLMJudge
from eval_harness.gate_executor import CommandSuiteRunner, frozen_gate_suite_digest, write_json_artifact
from eval_harness.probe_loader import frozen_digest, load_probe_set
from eval_harness.report import validate_report


def _collect(client, probe_set, probe_ids, *, target: dict) -> tuple[dict[str, str], list]:
    answers: dict[str, str] = {}
    results = []
    versionset_id = target["versionset_id"]
    content = target.get("content") or {}
    expected_digests = {
        "prompt_digest": (content.get("prompt") or {}).get("digest"),
        "kb_manifest_digest": (content.get("kb_manifest") or {}).get("manifest_digest"),
        "model_digest": (content.get("model") or {}).get("digest"),
    }
    for pid in probe_ids:
        result = client.evaluate_versionset(versionset_id, probe_set.get(pid).input)
        if result.status != "ok":
            raise RuntimeError(f"probe {pid} provider status={result.status!r}")
        if result.versionset_id != versionset_id:
            raise RuntimeError(
                f"probe {pid} executed against {result.versionset_id!r}, expected {versionset_id!r}"
            )
        observed_digests = {
            "prompt_digest": result.prompt_digest,
            "kb_manifest_digest": result.kb_manifest_digest,
            "model_digest": result.model_digest,
        }
        if observed_digests != expected_digests:
            raise RuntimeError(
                f"probe {pid} component digests do not match candidate VersionSet"
            )
        answers[pid] = result.answer
        results.append(result)
    return answers, results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--versionset-id", required=True)
    parser.add_argument("--out-dir", default="evidence/gate-live")
    args = parser.parse_args()

    settings = get_settings()
    probe_set = load_probe_set(settings.repo_root)
    client = QualityAPIClient(settings)
    digest = frozen_digest(probe_set)
    target = client.get_versionset(args.versionset_id)
    if target.get("versionset_id") != args.versionset_id:
        print("!! Quality API 返回了不同 VersionSet", file=sys.stderr)
        return 2
    probe_ids = [p.id for p in probe_set.probes]
    answers, chat_results = _collect(client, probe_set, probe_ids, target=target)

    # Refetch closes the race where lifecycle/revision changes during the probe run.
    target_after = client.get_versionset(args.versionset_id)
    if (
        target_after.get("digest") != target.get("digest")
        or target_after.get("revision") != target.get("revision")
    ):
        print("!! 候选 VersionSet 在评测期间发生漂移", file=sys.stderr)
        return 2

    athlete_digests = {item.model_digest for item in chat_results if item.model_digest}
    if len(athlete_digests) != 1:
        print(f"!! 候选回答未绑定单一 athlete model digest: {sorted(athlete_digests)}", file=sys.stderr)
        return 2
    athlete_digest = next(iter(athlete_digests))
    judge = None
    if settings.judge_model and settings.judge_model != settings.stepfun_model:
        judge = LLMJudge(settings, settings.judge_model)

    runner = GateRunner(settings, probe_set, judge=judge, frozen_probe_set_digest=digest)
    candidate = GateCandidate(
        target_versionset_digest=target["digest"],
        probe_set_digest=digest,
        regression_suite_digest=frozen_gate_suite_digest(settings.repo_root),
        answers=answers,
        provider_origins={
            probe_id: result.provider_origin
            for probe_id, result in zip(probe_ids, chat_results, strict=True)
        },
        athlete_model_digest=athlete_digest,
        source="live",
    )
    out = Path(args.out_dir)
    executor = CommandSuiteRunner(repo_root=settings.repo_root, evidence_dir=out, timeout_seconds=300)
    contract = executor.run(
        suite="contract-assets",
        kind="contract",
        argv=[sys.executable, "-m", "pytest", "contracts/conformance/test_schemas.py", "contracts/conformance/test_wilson.py", "-q"],
        artifact_name="contract-report.json",
    )
    replay = executor.run(
        suite="frozen-probe-replay",
        kind="replay",
        argv=[
            sys.executable,
            "-m",
            "pytest",
            "eval-harness/tests/unit/test_probe_judge.py",
            "eval-harness/tests/unit/test_digests.py",
            "eval-harness/tests/unit/test_gate.py",
            "-q",
        ],
        artifact_name="replay-report.json",
    )
    candidate_payload = {
        "target_versionset_id": target["versionset_id"],
        "target_revision": target["revision"],
        "target_versionset_digest": target["digest"],
        "dataset_id": probe_set.probe_set_id,
        "dataset_version": probe_set.version,
        "dataset_digest": digest,
        "responses": [
                {
                    "probe_id": probe_id,
                    "request_id": result.request_id,
                    "versionset_id": result.versionset_id,
                    "prompt_digest": result.prompt_digest,
                    "kb_manifest_digest": result.kb_manifest_digest,
                    "model_digest": result.model_digest,
                    "provider_origin": result.provider_origin,
                    "provider_status": result.status,
                    "trace_id": result.trace_id,
                    "answer": result.answer,
                }
                for probe_id, result in zip(probe_ids, chat_results, strict=True)
        ],
        "judge_responses": [],
    }
    candidate_ref = write_json_artifact(out / "candidate-answers.json", candidate_payload)
    report = runner.run(
        candidate,
        contract_result=contract.result,
        replay_result=replay.result,
        artifact_refs=[contract.artifact_ref, replay.artifact_ref, candidate_ref],
        live_available=True,
    )
    if judge is None or len(judge.evidence) != len(probe_set.probes):
        print("!! 裁判轨没有为每条冻结探针留下 provider receipt", file=sys.stderr)
        return 2
    candidate_payload["judge_responses"] = judge.evidence
    final_candidate_ref = write_json_artifact(out / "candidate-answers.json", candidate_payload)
    report["artifact_refs"] = [
        final_candidate_ref if ref.get("uri") == candidate_ref["uri"] else ref
        for ref in report["artifact_refs"]
    ]

    out.mkdir(parents=True, exist_ok=True)
    (out / "gate-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overall_status": report["overall_status"],
                      "rule": report["rule_track"]["status"],
                      "judge": report["judge_track"]["status"],
                      "deterministic": report["deterministic_tests"]["status"],
                      "live": report["live_provider_e2e"]["status"]}, indent=2))
    errs = validate_report(report, "gate-report.schema.json")
    print("schema:", "OK" if not errs else errs)
    if errs:
        return 1
    return 0 if report["overall_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
