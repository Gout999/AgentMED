#!/usr/bin/env python3
"""校验 agents/team.yaml 内联 soul 与 agents/souls/<name>.md 逐字一致。

用法：python3 agents/scripts/verify-soul-sync.py
退出码：0=一致；1=不一致（列出 diff 的 worker 与首处差异行）。

维护纪律：改 SOUL 正文必须同步 team.yaml 对应 Worker 的 soul 块；本脚本防漂移。
"""
from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]  # agents/
TEAM = ROOT / "team.yaml"
SOULS = ROOT / "souls"


def main() -> int:
    ok = True
    docs = yaml.safe_load_all(TEAM.read_text(encoding="utf-8"))
    for doc in docs:
        if not doc or doc.get("kind") != "Worker":
            continue
        name = doc["metadata"]["name"]
        inline = doc["spec"]["soul"]
        soul_file = SOULS / f"{name}.md"
        if not soul_file.exists():
            print(f"MISSING soul file: {soul_file.relative_to(ROOT)}")
            ok = False
            continue
        file_text = soul_file.read_text(encoding="utf-8")
        if file_text != inline:
            ok = False
            print(f"SOUL MISMATCH: {name}")
            for i, (a, b) in enumerate(zip(inline.splitlines(), file_text.splitlines())):
                if a != b:
                    print(f"  first diff line {i}:")
                    print(f"    team.yaml : {a!r}")
                    print(f"    souls/*.md: {b!r}")
                    break
            else:
                print(f"  (长度不同: inline={len(inline)} file={len(file_text)})")
    print("soul-sync OK" if ok else "soul-sync FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
