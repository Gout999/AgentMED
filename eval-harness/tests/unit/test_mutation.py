"""变异巡检单测：算子库 ≥6、检出率统计、离线 FakeLLM 端到端。"""
import pytest

from eval_harness.mutation import MutationOperatorLibrary, MutationPatrol


class FakeLLM:
    """按 system prompt 内容/参数返回确定性的「变异响应」，用于离线验证检出逻辑。"""

    def __init__(self, probe_set, samples):
        self.probe_set = probe_set
        self.samples = samples
        self.baseline_answers = samples["states"]["baseline"]

    def chat(self, system: str, user: str, *, model=None, params=None):
        p = params or {}
        if p.get("temperature", 0) > 0.5:
            return self._resp("输出不够规范，但这里是一大段啰嗦的话没有使用 JSON 结构哦！" * 3, "step-3.7-flash")
        if p.get("max_tokens", 1024) < 100:
            return self._resp("7天无理由，运费我们承担。（截断", "step-3.7-flash")

        prompt_part, _, kb_part = system.partition("## 知识库资料")
        # prompt 改写（只查 prompt 段，避免命中 KB 中的同词）
        if "退货需经人工审核" in prompt_part:
            return self._resp("退货需经人工审核，已激活商品不支持退货。", "step-3.7-flash")
        if "运费由顾客承担" in prompt_part:
            return self._resp("退货运费由顾客承担。", "step-3.7-flash")
        if "72 小时内发货" in prompt_part:
            return self._resp("现货下单后 72 小时内发货。", "step-3.7-flash")
        if "7 天无理由退货" not in prompt_part:
            return self._resp("可以申请退货，具体以审核为准。", "step-3.7-flash")
        # 知识过时化（只查 KB 段；「总续航 8 小时」避免撞「48 小时内」）
        if "总续航 8 小时" in kb_part:
            return self._resp("X200 蓝牙耳机总续航 8 小时。", "step-3.7-flash")
        if "10000mAh" in kb_part:
            return self._resp("Z30 充电宝容量为 10000mAh。", "step-3.7-flash")

        # 默认走录制基线答案（按探针输入匹配）
        pid = self._find_probe_id(user)
        ans = self.baseline_answers.get(pid, {}).get("answer", "支持 7 天无理由退货。")
        return self._resp(ans, "step-3.7-flash")

    def _find_probe_id(self, user: str) -> str | None:
        for p in self.probe_set.probes:
            if p.input == user:
                return p.id
        return None

    def _resp(self, content: str, model: str):
        from eval_harness.llm import LLMResponse
        return LLMResponse(
            content=content,
            model=model,
            model_digest="sha256:" + "f" * 64,
            request_id="req_mutation_fixture",
            usage={},
        )


def _patrol(settings, probe_set, samples) -> MutationPatrol:
    llm = FakeLLM(probe_set, samples)
    return MutationPatrol(settings, probe_set, llm=llm)


def test_operator_library_has_at_least_six(repo_root):
    lib = MutationOperatorLibrary(repo_root)
    cases = lib.all_cases()
    assert len(cases) >= 6
    layers = {c.layer for c in cases}
    assert {"prompt", "kb", "model_params"} <= layers


def test_operator_library_layers_and_targets(repo_root):
    lib = MutationOperatorLibrary(repo_root)
    by_id = {c.operator_id: c for c in lib.all_cases()}
    assert by_id["prompt-deny-refund"].target_probes == ["cs-001", "cs-002"]
    assert by_id["kb-outdate-battery"].layer == "kb"
    assert by_id["param-high-temperature"].layer == "model_params"
    assert by_id["prompt-remove-keyword"].target_probes == ["cs-004", "cs-005"]


def test_baseline_system_contains_keyword(repo_root):
    lib = MutationOperatorLibrary(repo_root)
    assert "7 天无理由" in lib.baseline_system()


def test_patrol_detects_known_mutations(settings, probe_set, probe_samples, repo_root):
    result = _patrol(settings, probe_set, probe_samples).run(repo_root)
    # prompt-deny-refund / kb-outdate-battery 必须被检出
    detected_ops = {c["operator_id"] for c in result.cases if c["detected"]}
    assert "prompt-deny-refund" in detected_ops
    assert "kb-outdate-battery" in detected_ops
    assert "kb-wrong-capacity" in detected_ops
    # 检出率在 (0,1]
    assert 0.0 < result.summary["detection_rate"] <= 1.0
    assert result.summary["detected"] >= 4


def test_patrol_markdown(settings, probe_set, probe_samples, repo_root):
    result = _patrol(settings, probe_set, probe_samples).run(repo_root)
    md = result.to_markdown()
    assert "变异巡检报告" in md
    assert "检出率" in md
    assert f"{result.summary['detection_rate']:.2%}" in md


def test_param_drift_detected(settings, probe_set, probe_samples, repo_root):
    result = _patrol(settings, probe_set, probe_samples).run(repo_root)
    param_cases = [c for c in result.cases if c["layer"] == "model_params"]
    assert param_cases, "必须有参数漂移算子"
    assert all("param-" in c["operator_id"] for c in param_cases)
