"""沙箱验证 runner（宿主机侧）：隔离容器跑坏例回放，修前/修后对照 + 判定。

流程：
  1. docker run 隔离容器（本地 copaw-worker 镜像，只读挂载 replay.py + 探针）
  2. 修前回放（原 prompt）→ 修后回放（候选 prompt）
  3. probe 判定（must_include 连续子序列 / must_not_include）
  4. 输出证据 JSON：{before: {pass, output, digest}, after: {...}, verdict: PASS/FAIL}

用法：
  python3 runner.py --probe probe.json --prompt-before p1.md --prompt-after p2.md --out evidence.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

IMAGE = "agent-station/copaw-worker:s0-acceptance-v123"
MODEL_URL = "http://host.docker.internal:8089"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize(text: str) -> str:
    """与仓库权威探针判定（probe_judge.normalize_text）对齐：去全部空白。

    模型常写「7天无理由」而规则是「7 天无理由」——空白归一化后按连续子串判定，
    避免良基答案被机器误杀。"""
    return "".join(text.split())


def _is_subsequence(needle: str, haystack: str) -> bool:
    """needle 是否为 haystack 的有序子序列（与 probe_judge._is_subsequence 同口径）。"""
    it = iter(haystack)
    return all(ch in it for ch in needle)


def _contains(phrase: str, normalized_answer: str) -> bool:
    norm = _normalize(str(phrase))
    if not norm:
        return True
    if norm in normalized_answer:
        return True
    return _is_subsequence(norm, normalized_answer)


def _judge(output: str, probe: dict) -> bool:
    rules = probe.get("judge", {})
    must = rules.get("must_include")
    must_not = rules.get("must_not_include")
    norm = _normalize(output)
    # 放行侧（must_include）：连续子串或有序子序列（覆盖「7天内都可以无理由」这类
    # 自然改写）；严格侧（must_not_include）：只认连续子串，防漏检。
    if must and not _contains(must, norm):
        return False
    if must_not and _normalize(str(must_not)) in norm:
        return False
    if not must and not must_not:
        return bool(output.strip())
    return True


def run_replay(workdir: Path, prompt_file: Path, probe_file: Path) -> dict:
    out = workdir / "replay-out.json"
    cmd = [
        "docker", "run", "--rm",
        "--entrypoint", "python3",
        "--network", "bridge",
        "-v", f"{workdir}:/sandbox:ro",
        "-v", f"{out.parent}:/sandbox-out",
        IMAGE,
        "/sandbox/replay.py",
        MODEL_URL, f"/sandbox/{prompt_file.name}", f"/sandbox/{probe_file.name}",
        f"/sandbox-out/{out.name}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if out.exists():
        return json.loads(out.read_text())
    detail = (proc.stderr or proc.stdout or "")[:400]
    return {"ok": False, "output": "", "error": "sandbox replay produced no output: " + detail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", required=True)
    parser.add_argument("--prompt-before", required=True)
    parser.add_argument("--prompt-after", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    probe = json.loads(Path(args.probe).read_text())
    with tempfile.TemporaryDirectory(dir="/tmp") as td:
        td = Path(td)
        for f in (args.probe, args.prompt_before, args.prompt_after):
            (td / Path(f).name).write_bytes(Path(f).read_bytes())
        (td / "replay.py").write_bytes(Path(__file__).parent.joinpath("replay.py").read_bytes())
        before = run_replay(td, Path(Path(args.prompt_before).name), Path(Path(args.probe).name))
        after = run_replay(td, Path(Path(args.prompt_after).name), Path(Path(args.probe).name))
    before_pass = before["ok"] and _judge(before.get("output", ""), probe)
    after_pass = after["ok"] and _judge(after.get("output", ""), probe)
    verdict = "PASS" if (not before_pass and after_pass) else "FAIL"
    evidence = {
        "schema_version": "1.0",
        "probe_digest": sha256(Path(args.probe)),
        "prompt_before_digest": sha256(Path(args.prompt_before)),
        "prompt_after_digest": sha256(Path(args.prompt_after)),
        "before": {"pass": before_pass, "output": before.get("output", "")[:500], "error": before.get("error", "")},
        "after": {"pass": after_pass, "output": after.get("output", "")[:500], "error": after.get("error", "")},
        "verdict": verdict,
    }
    Path(args.out).write_text(json.dumps(evidence, ensure_ascii=False, indent=1))
    print(json.dumps(evidence, ensure_ascii=False, indent=1))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
