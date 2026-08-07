"""探针判定单测：must_include（连续/子序列）、must_not_include、format=json、max_output_chars。"""
import pytest

from eval_harness.models import Probe
from eval_harness.probe_judge import judge_probe, normalize_text


def probe(**kw) -> Probe:
    defaults = dict(
        id="t-001", input="q", description="d",
        must_include=(), must_not_include=(), format=None, max_output_chars=None,
    )
    defaults.update(kw)
    return Probe(**defaults)


def test_must_include_exact():
    p = probe(must_include=("7 天无理由", "退"))
    assert judge_probe(p, "我们支持 7 天无理由退货")[0] is True


def test_must_include_whitespace_insensitive():
    p = probe(must_include=("7 天无理由",))
    assert judge_probe(p, "7天无理由退货")[0] is True
    assert judge_probe(p, "7 天 无 理 由 退 货")[0] is True


def test_must_include_subsequence_paraphrase():
    """模型把「7 天无理由」改写成「7 天内…无理由」时，子序列应命中（放行侧宽容）。"""
    p = probe(must_include=("7 天无理由",))
    assert judge_probe(p, "签收次日起 7 天内都可以申请无理由退货")[0] is True


def test_must_not_include_fails():
    p = probe(must_not_include=("不支持退货", "人工审核"))
    assert judge_probe(p, "退货需经人工审核，已激活商品不支持退货")[0] is False


def test_must_not_include_takes_precedence_over_subsequence():
    """即使子序列放行了 must_include，must_not_include 命中仍判 fail。"""
    p = probe(must_include=("7 天无理由",), must_not_include=("不支持退货",))
    ans = "7天内不支持退货，无理由退款不适用"
    ok, reasons = judge_probe(p, ans)
    assert ok is False
    assert any("不支持退货" in r for r in reasons)


def test_json_format_valid():
    p = probe(format="json", must_include=("battery", "anc", "bluetooth"))
    ans = '{"battery": "30h", "anc": "42db", "bluetooth": "5.3"}'
    assert judge_probe(p, ans)[0] is True


def test_json_format_missing_key():
    p = probe(format="json", must_include=("battery", "anc", "bluetooth"))
    ans = '{"battery": "30h"}'
    ok, reasons = judge_probe(p, ans)
    assert ok is False
    assert any("必需键" in r for r in reasons)


def test_json_format_with_fence():
    p = probe(format="json", must_include=("battery",))
    ans = '```json\n{"battery": "30h"}\n```'
    assert judge_probe(p, ans)[0] is True


def test_json_format_invalid():
    p = probe(format="json", must_include=())
    assert judge_probe(p, "不是 JSON")[0] is False


def test_max_output_chars():
    p = probe(must_include=("7 天无理由",), max_output_chars=60)
    assert judge_probe(p, "7 天无理由退货，激活后仍可退，运费由我们承担。")[0] is True
    long_ans = "7 天无理由" + "很长" * 40
    assert judge_probe(p, long_ans)[0] is False


def test_empty_answer_fails():
    p = probe(must_include=("7 天无理由",))
    assert judge_probe(p, "")[0] is False


def test_normalize_text():
    assert normalize_text("签收次日 7 天内\n都可以") == "签收次日7天内都可以"
