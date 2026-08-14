"""沙箱内回放器：在隔离容器里把（prompt + 坏例输入）打到模型路径，输出结果 JSON。

用法（容器内）：
  python3 replay.py <model_url> <prompt_file> <input_file> <output_file>

输出 JSON：{ok, output, model, usage?, error}
"""
import json
import sys
import urllib.request


def main() -> int:
    model_url, prompt_file, input_file, output_file = sys.argv[1:5]
    prompt = open(prompt_file).read()
    badcase = json.load(open(input_file))
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": badcase.get("input", "")},
    ]
    payload = {
        "model": badcase.get("model", "step-3.7-flash"),
        "messages": messages,
        "max_tokens": badcase.get("max_tokens", 64),
    }
    req = urllib.request.Request(
        model_url + "/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    result = {"ok": False, "output": "", "model": payload["model"], "error": ""}
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode())
            choice = (body.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            result["ok"] = True
            # 判定只看最终回答 content；reasoning 单独留证，不参与判定
            result["output"] = msg.get("content") or ""
            result["reasoning"] = msg.get("reasoning_content") or ""
            result["usage"] = body.get("usage")
    except Exception as exc:  # noqa: BLE001 - 沙箱内任何失败都落 error 字段
        result["error"] = f"{type(exc).__name__}: {exc}"[:400]
    open(output_file, "w").write(json.dumps(result, ensure_ascii=False))
    print(json.dumps(result, ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
