#!/usr/bin/env python
"""CLI：单次变异巡检（算子库 → 探测用例 → 攻击 → 检出率统计 + Markdown 周报）。

用法：.venv/bin/python scripts/run_mutation.py [--out-dir evidence]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval_harness.config import get_settings
from eval_harness.llm import LLMClient
from eval_harness.mutation import MutationPatrol
from eval_harness.probe_loader import load_probe_set
from eval_harness.weekly import WeeklyInput, build_weekly_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="evidence")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.has_stepfun_key:
        print("!! STEPFUN_API_KEY 未配置", file=sys.stderr)
        return 2

    probe_set = load_probe_set(settings.repo_root)
    patrol = MutationPatrol(settings, probe_set, LLMClient(settings))
    result = patrol.run(settings.repo_root)

    out = Path(args.out_dir) / "patrol"
    out.mkdir(parents=True, exist_ok=True)
    (out / "patrol-report.md").write_text(result.to_markdown(), encoding="utf-8")
    print(result.to_markdown())
    print(f"== 落盘: {(out / 'patrol-report.md').resolve()} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
