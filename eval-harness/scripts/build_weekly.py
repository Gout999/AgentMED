#!/usr/bin/env python
"""CLI：质量周报生成（从已落盘的实验/门禁/巡检结果聚合）。

用法：.venv/bin/python scripts/build_weekly.py --period 2026-W32 [--evidence-dir evidence]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_harness.weekly import WeeklyInput, build_weekly_json, build_weekly_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="2026-W32")
    parser.add_argument("--evidence-dir", default="evidence")
    args = parser.parse_args()

    ev = Path(args.evidence_dir)
    data = WeeklyInput(period=args.period)

    # 变异巡检
    patrol_md = ev / "patrol" / "patrol-report.md"
    if patrol_md.exists():
        text = patrol_md.read_text(encoding="utf-8")
        import re
        m = re.search(r"变异用例：(\d+).*?检出 (\d+) /", text)
        if m:
            data.mutation_cases_generated = int(m.group(1))
            data.mutation_detected = int(m.group(2))

    # 归因实验（含 ground-truth 命中率；扫描全部 exp_* 目录）
    attr_reports = list(ev.glob("exp_*/attribution-report.json"))
    for p in attr_reports:
        rep = json.loads(p.read_text(encoding="utf-8"))
        data.attribution_experiments += 1
        if rep["verdict"]["decision"] == "ATTRIBUTED":
            data.attribution_attributed += 1
            if rep["verdict"]["attributed_layer"] == "prompt":
                data.attribution_ground_truth_hits += 1

    # 门禁：passed=一次通过；failed=被拦截；error（如裁判模型缺失/live 不可用）=基础设施转人工，不计入拦截
    for gate_json in ev.glob("gate-*/gate-report.json"):
        gate = json.loads(gate_json.read_text(encoding="utf-8"))
        data.gate_runs += 1
        if gate["overall_status"] == "failed":
            data.gate_blocked += 1
        elif gate["overall_status"] == "passed":
            data.gate_first_pass += 1

    md = build_weekly_report(data)
    out = ev / f"weekly-{args.period}.md"
    out.write_text(md, encoding="utf-8")
    (ev / f"weekly-{args.period}.json").write_text(
        json.dumps(build_weekly_json(data), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(md)
    print(f"== 落盘: {out.resolve()} / weekly-{args.period}.json ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
