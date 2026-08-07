"""探针判定规则（确定性）：expected_behavior + must_include / must_not_include /
format=json / max_output_chars。返回 (passed, reasons)。

匹配口径（对中文 RAG 客服的机器判定做归一化，全部确定性）：
- 先做空白归一化：答案与规则字符串都去掉全部空白（中文分词后 LLM 常插入语气词，
  如「7 天内都可以申请无理由」相对 must_include「7 天无理由」就是非连续形态）。
- must_include：命中条件 = 规则串是归一化答案的**连续子串**，或**有序子序列**
  （允许中间插入语气词/连接词，但各字必须按序出现）——覆盖「7天内…无理由退货」这类自然改写。
- must_not_include：归一化答案**连续子串**命中即 fail（严格侧，防漏检）。
- format=json：必须可解析且 must_include 的键齐全。
- max_output_chars：对原始答案长度做上限约束。

注意：子序列匹配是「放行侧」宽容（防止良基答案误杀），must_not_include 与
format 约束仍是严格侧，组合起来不会让坏答案漏过（见 test_probe_judge 的对抗用例）。
"""
from __future__ import annotations

import json
import re
import unicodedata

from .models import Probe

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """去空白 + 全角→半角 + NFKC 归一化（中文客服文本机器比对用）。"""
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub("", text)


def _is_subsequence(needle: str, haystack: str) -> bool:
    """needle 是否为 haystack 的有序子序列（贪心匹配）。"""
    it = iter(haystack)
    return all(ch in it for ch in needle)


def _contains_phrase(phrase: str, normalized_answer: str) -> bool:
    norm_phrase = normalize_text(phrase)
    if not norm_phrase:
        return True
    if norm_phrase in normalized_answer:
        return True
    return _is_subsequence(norm_phrase, normalized_answer)


def _strip_json_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def _check_json(probe: Probe, answer: str) -> tuple[bool, str]:
    cleaned = _strip_json_fence(answer)
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return False, f"输出不是合法 JSON: {exc}"
    if isinstance(obj, dict):
        keys = set(obj.keys())
    elif isinstance(obj, list):
        keys = set()
        for item in obj:
            if isinstance(item, dict):
                keys |= set(item.keys())
    else:
        return False, "JSON 顶层应为对象或数组"
    missing = [k for k in probe.must_include if k not in keys]
    if missing:
        return False, f"JSON 缺少必需键: {missing}"
    return True, "JSON 合法且键齐全"


def judge_probe(probe: Probe, answer: str) -> tuple[bool, list[str]]:
    """判定单次调用 pass/fail。answer 为 chat 返回正文（或兜底文案）。"""
    reasons: list[str] = []
    raw_answer = (answer or "").strip()
    norm_answer = normalize_text(raw_answer)

    if probe.is_format_json:
        ok, msg = _check_json(probe, raw_answer)
        if not ok:
            reasons.append(msg)
    else:
        missing = [s for s in probe.must_include if not _contains_phrase(s, norm_answer)]
        if missing:
            reasons.append(f"缺少 must_include: {missing}")

    for s in probe.must_not_include:
        norm_s = normalize_text(s)
        if norm_s and norm_s in norm_answer:
            reasons.append(f"命中 must_not_include: {s!r}")

    if probe.max_output_chars is not None and len(raw_answer) > probe.max_output_chars:
        reasons.append(f"输出过长: {len(raw_answer)} > {probe.max_output_chars}")

    if reasons:
        return False, reasons
    return True, []
