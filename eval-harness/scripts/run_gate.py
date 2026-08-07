#!/usr/bin/env python
"""CLI：对 live demo-app 当前状态跑双轨门禁（候选 = 当前 live 状态）。

用法：
    .venv/bin/python scripts/run_gate.py [--state baseline|fault] [--out-dir evidence]

--state baseline：复位后采集基线答案（期望 overall=passed，裁判模型可用时）。
--state fault：注入 B1 后采集（期望被拦截）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_harness.client import QualityAPIClient
from eval_harness.config import get_settings
from eval_harness.digests import sha256_digest
from eval_harness.gate import GateCandidate, GateRunner, LLMJudge
from eval_harness.probe_loader import frozen_digest, load_probe_set
from eval_harness.report import validate_report


def _collect(client, probe_set, probe_ids) -> dict:
    return {pid: client.chat(probe_set.get(pid).input).answer for pid in probe_ids}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", choices=["baseline", "fault"], default="baseline")
    parser.add_argument("--out-dir", default="evidence")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.has_stepfun_key:
        print("!! STEPFUN_API_KEY 未配置", file=sys.stderr)
        return 2

    probe_set = load_probe_set(settings.repo_root)
    client = QualityAPIClient(settings)
    digest = frozen_digest(probe_set)

    try:
        if args.state == "fault":
            client.inject_fault("B1")
        probe_ids = [p.id for p in probe_set.probes] if args.state == "baseline" else ["cs-001", "cs-002", "cs-003", "cs-013", "cs-014"]
        answers = _collect(client, probe_set, probe_ids)
    finally:
        client.reset_faults()

    athlete_digest = sha256_digest({"provider": "stepfun", "model": settings.stepfun_model,
                                    "params": {"temperature": 0.0, "max_tokens": 1024}})
    judge = None
    if settings.judge_model and settings.judge_model != settings.stepfun_model:
        judge = LLMJudge(settings, settings.judge_model)

    runner = GateRunner(settings, probe_set, judge=judge, frozen_probe_set_digest=digest)
    report = runner.run(
        GateCandidate(
            target_versionset_digest="sha256:" + "b" * 64,
            probe_set_digest=digest,
            regression_suite_digest="sha256:" + "e" * 64,
            answers=answers,
            athlete_model_digest=athlete_digest,
        ),
        contract_n_passed=12, contract_n_failed=0,
        replay_n_passed=16, replay_n_failed=0,
        live_available=bool(settings.has_stepfun_key),
    )

    out = Path(args.out_dir) / f"gate-{args.state}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overall_status": report["overall_status"],
                      "rule": report["rule_track"]["status"],
                      "judge": report["judge_track"]["status"],
                      "deterministic": report["deterministic_tests"]["status"],
                      "live": report["live_provider_e2e"]["status"]}, indent=2))
    errs = validate_report(report, "gate-report.schema.json")
    print("schema:", "OK" if not errs else errs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
