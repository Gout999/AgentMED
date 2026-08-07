#!/usr/bin/env python
"""CLI：对 live demo-app 跑 B1 5-cell 对照实验，产出 evidence-bundle + attribution-report。

用法：
    .venv/bin/python scripts/run_b1_experiment.py [--reps 3] [--seed 20260807] [--out-dir evidence]

前置：demo-app 运行于 CASELOOP_QUALITY_API_BASE_URL（默认 127.0.0.1:8080）；
      STEPFUN_API_KEY 已配置（live 调用）。
运行后自动复位故障；主控验收后还应执行 bash demo-app/scripts/reset_state.sh。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_harness.client import QualityAPIClient
from eval_harness.config import get_settings
from eval_harness.experiment import DemoAppB1Driver, ExperimentRunner
from eval_harness.models import ExperimentPlan
from eval_harness.probe_loader import frozen_digest, load_probe_set
from eval_harness.report import validate_report


def main() -> int:
    parser = argparse.ArgumentParser(description="B1 5-cell 对照实验")
    parser.add_argument("--reps", type=int, default=None, help="每 cell 每探针重复次数（默认取 fixtures/b1 冻结值 3）")
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--out-dir", default="evidence", help="证据输出目录（相对 eval-harness）")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.has_stepfun_key:
        print("!! STEPFUN_API_KEY 未配置，live 实验无法执行", file=sys.stderr)
        return 2

    probe_set = load_probe_set(settings.repo_root)
    client = QualityAPIClient(settings)
    runner = ExperimentRunner(client, probe_set, settings)
    digest = frozen_digest(probe_set)

    reps = args.reps
    if reps is None:
        import yaml
        raw = yaml.safe_load((settings.repo_root / "contracts/fixtures/b1-prompt-regression.yaml").read_text(encoding="utf-8"))
        reps = int(raw["experiment_protocol"]["repetitions"])

    plan = ExperimentPlan(
        experiment_id="exp_b1run0000000000000001",
        case_id="case_b1run0000000000000001",
        matrix="five_cell",
        repetitions=reps,
        confidence=0.95,
        delta_min=settings.experiment_delta_min,
        probe_set_digest=digest,
        version_digests={},
        discovery=["cs-001", "cs-002", "cs-003"],
        hidden_confirmation=["cs-004", "cs-005"],
        unaffected_controls=["cs-013", "cs-014", "cs-015", "cs-016"],
        random_seed=args.seed,
    )

    try:
        res = runner.run(plan, DemoAppB1Driver(client), seed=args.seed)
    finally:
        try:
            client.reset_faults()
        except Exception:
            pass

    out = Path(args.out_dir) / plan.experiment_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence-bundle.json").write_text(json.dumps(res.bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "attribution-report.json").write_text(json.dumps(res.report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("== 裁决 ==")
    print(json.dumps(res.verdict, ensure_ascii=False, indent=2))
    print("== 各臂恢复率 ==")
    for arm in ("C", "RP", "RK", "RM", "G"):
        print(f"  {arm}: recovery={res.bundle['cells'][arm]['recovery_rate']} control={res.bundle['cells'][arm]['control_pass_rate']}")
    print("== 效应量 ==")
    for k in ("prompt", "kb", "model_params"):
        e = res.bundle["effects"][k]
        print(f"  {k}: Δ={e['delta']} 95%CI[{e['ci95_lower']},{e['ci95_upper']}] significant={e['significant']}")
    print("== schema 校验 ==")
    for name, obj in [("evidence-bundle.schema.json", res.bundle), ("attribution-report.schema.json", res.report)]:
        errs = validate_report(obj, name)
        print(f"  {name}: {'OK' if not errs else errs}")
    print(f"== 证据落盘: {out.resolve()} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
