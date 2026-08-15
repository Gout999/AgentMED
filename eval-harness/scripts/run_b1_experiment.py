#!/usr/bin/env python
"""CLI：对两个 immutable VersionSet 跑 live B1 5-cell 归因实验。

用法：
    .venv/bin/python scripts/run_b1_experiment.py [--reps 3] [--seed 20260807] [--out-dir evidence]

前置：demo-app 运行于 AGENTMED_QUALITY_API_BASE_URL（默认 127.0.0.1:8080）；
      STEPFUN_API_KEY 已配置（live 调用）。
VersionSet 必须预先由 Release Controller 创建；本命令没有 Quality 写权限。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_harness.client import QualityAPIClient
from eval_harness.config import get_settings
from eval_harness.experiment import ImmutableVersionSetDriver, ExperimentRunner
from eval_harness.models import ExperimentPlan
from eval_harness.probe_loader import frozen_digest, load_probe_set
from eval_harness.report import validate_report


def main() -> int:
    parser = argparse.ArgumentParser(description="B1 5-cell 对照实验")
    parser.add_argument("--reps", type=int, default=None, help="每 cell 每探针重复次数（默认取 fixtures/b1 冻结值 3）")
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument("--out-dir", default="evidence", help="证据输出目录（相对 eval-harness）")
    parser.add_argument("--bad-versionset-id", required=True, help="active bad P1 VersionSet")
    parser.add_argument("--good-versionset-id", required=True, help="known-good P0 VersionSet")
    parser.add_argument("--case-id", required=True, help="authoritative control-plane Case id")
    parser.add_argument("--experiment-id", required=True, help="authoritative frozen Experiment id")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.has_stepfun_key:
        print("!! STEPFUN_API_KEY 未配置，live 实验无法执行", file=sys.stderr)
        return 2

    probe_set = load_probe_set(settings.repo_root)
    client = QualityAPIClient(settings)
    evidence_root = Path(args.out_dir).resolve()
    digest = frozen_digest(probe_set)

    bad_ref = client.get_versionset(args.bad_versionset_id)
    good_ref = client.get_versionset(args.good_versionset_id)
    if (
        bad_ref.get("versionset_id") != args.bad_versionset_id
        or good_ref.get("versionset_id") != args.good_versionset_id
        or bad_ref.get("status") != "active"
        or bad_ref.get("digest") == good_ref.get("digest")
    ):
        print("!! bad/good VersionSet identity, lifecycle, or digest is invalid", file=sys.stderr)
        return 2
    cell_versionset_refs = {
        "C": bad_ref,
        "RP": good_ref,
        "RK": bad_ref,
        "RM": bad_ref,
        "G": good_ref,
    }
    runner = ExperimentRunner(
        client,
        probe_set,
        settings,
        artifact_dir=evidence_root,
        cell_versionset_refs=cell_versionset_refs,
    )

    reps = args.reps
    if reps is None:
        import yaml
        raw = yaml.safe_load((settings.repo_root / "contracts/fixtures/b1-prompt-regression.yaml").read_text(encoding="utf-8"))
        reps = int(raw["experiment_protocol"]["repetitions"])

    plan = ExperimentPlan(
        experiment_id=args.experiment_id,
        case_id=args.case_id,
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

    driver = ImmutableVersionSetDriver(
        {
            "C": args.bad_versionset_id,
            "RP": args.good_versionset_id,
            "RK": args.bad_versionset_id,
            "RM": args.bad_versionset_id,
            "G": args.good_versionset_id,
        }
    )
    res = runner.run(plan, driver, seed=args.seed)

    out = evidence_root / plan.experiment_id
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / "evidence-bundle.json"
    bundle_path.write_text(json.dumps(res.bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    res.report["evidence_bundle_ref"]["uri"] = bundle_path.resolve().as_uri()
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
    schema_errors = {}
    for name, obj in [("evidence-bundle.schema.json", res.bundle), ("attribution-report.schema.json", res.report)]:
        errs = validate_report(obj, name)
        schema_errors[name] = errs
        print(f"  {name}: {'OK' if not errs else errs}")
    valid = not any(schema_errors.values())
    attributed_prompt = (
        res.verdict.get("decision") == "ATTRIBUTED"
        and res.verdict.get("attributed_layer") == "prompt"
    )
    (out / "run-result.json").write_text(
        json.dumps(
            {
                "case_id": args.case_id,
                "experiment_id": args.experiment_id,
                "schema_valid": valid,
                "schema_errors": schema_errors,
                "decision": res.verdict.get("decision"),
                "attributed_layer": res.verdict.get("attributed_layer"),
                "evidence_bundle": bundle_path.resolve().as_uri(),
                "attribution_report": (out / "attribution-report.json").resolve().as_uri(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"== 证据落盘: {out.resolve()} ==")
    if not valid:
        print("!! attribution schemas are invalid", file=sys.stderr)
        return 1
    if not attributed_prompt:
        print("!! B1 live attribution did not adjudicate ATTRIBUTED/prompt", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
