"""LLMClient / LLMJudge 裁判第二 provider 覆盖单测（独立 OpenAI 兼容 endpoint）。

契约：
- 未同时设置 JUDGE_API_KEY + JUDGE_BASE_URL 时行为与旧版完全一致
  （沿用 StepFun 运动员链路，provider 标签 "stepfun"）；
- 两者同时设置时裁判走独立 endpoint/key，provider 标签进入 model_digest；
- 裁判打分参数 SCORE_PARAMS = {"temperature": 0.0, "max_tokens": 1024}
  （reasoning 模型如 glm-5.2 的思考链也消耗 completion token，256 会烧满）。
"""
from eval_harness.config import Settings
from eval_harness.digests import sha256_digest
from eval_harness.gate import LLMJudge
from eval_harness.llm import LLMClient

_GATEWAY = "https://api-gateway.openagents.org/v1"
_STEPFUN = "https://api.stepfun.com/step_plan/v1"


def _settings(**over) -> Settings:
    """确定性 Settings：judge_* 显式置空，避免受开发者本机 env 影响。"""
    base = dict(
        stepfun_api_key="sk-athlete",
        stepfun_base_url=_STEPFUN,
        stepfun_model="step-3.7-flash",
        judge_model="glm-5.2",
        judge_api_key="",
        judge_base_url="",
        judge_provider="stepfun",
    )
    base.update(over)
    return Settings(**base)


def _override_settings() -> Settings:
    return _settings(
        judge_api_key="sk-judge",
        judge_base_url=_GATEWAY,
        judge_provider="openagents",
    )


# ------------------------------------------------------------------ Settings 解析
def test_judge_provider_override_requires_both_key_and_url():
    assert _settings().has_judge_provider_override is False
    assert _settings(judge_api_key="sk-judge").has_judge_provider_override is False
    assert _settings(judge_base_url=_GATEWAY).has_judge_provider_override is False
    assert _override_settings().has_judge_provider_override is True


def test_judge_provider_fields_read_from_env(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "sk-judge")
    monkeypatch.setenv("JUDGE_BASE_URL", _GATEWAY)
    monkeypatch.setenv("JUDGE_PROVIDER", "openagents")
    s = Settings()
    assert s.judge_api_key == "sk-judge"
    assert s.judge_base_url == _GATEWAY
    assert s.judge_provider == "openagents"
    assert s.has_judge_provider_override is True


def test_judge_provider_defaults(monkeypatch):
    for name in ("JUDGE_API_KEY", "JUDGE_BASE_URL", "JUDGE_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert s.judge_api_key == ""
    assert s.judge_base_url == ""
    assert s.judge_provider == "stepfun"
    assert s.has_judge_provider_override is False


# ------------------------------------------------------------------ LLMClient
def test_client_defaults_to_stepfun_backwards_compatible():
    client = LLMClient(_settings())
    assert client.provider == "stepfun"
    assert client._client.api_key == "sk-athlete"
    assert str(client._client.base_url).rstrip("/") == _STEPFUN


def test_client_override_applied():
    client = LLMClient(
        _settings(),
        api_key="sk-judge",
        base_url=_GATEWAY,
        provider="openagents",
    )
    assert client.provider == "openagents"
    assert client._client.api_key == "sk-judge"
    assert str(client._client.base_url).rstrip("/") == _GATEWAY


def test_provider_label_enters_model_digest():
    params = {"temperature": 0.0, "max_tokens": 1024}
    default_digest = LLMClient(_settings()).model_digest_for("glm-5.2", params)
    # 旧版口径：provider 标签硬编码 "stepfun"——回退路径必须逐字节一致
    assert default_digest == sha256_digest(
        {"provider": "stepfun", "model": "glm-5.2", "params": params}
    )
    override_digest = LLMClient(
        _settings(), api_key="sk-judge", base_url=_GATEWAY, provider="openagents"
    ).model_digest_for("glm-5.2", params)
    assert override_digest == sha256_digest(
        {"provider": "openagents", "model": "glm-5.2", "params": params}
    )
    assert override_digest != default_digest


# ------------------------------------------------------------------ LLMJudge
def test_judge_score_params_carry_reasoning_budget():
    assert LLMJudge.SCORE_PARAMS == {"temperature": 0.0, "max_tokens": 1024}


def test_judge_falls_back_to_stepfun_without_override():
    s = _settings()
    judge = LLMJudge(s, s.judge_model)
    assert judge.llm.provider == "stepfun"
    assert judge.llm._client.api_key == "sk-athlete"
    assert judge.model_digest == sha256_digest(
        {"provider": "stepfun", "model": "glm-5.2", "params": LLMJudge.SCORE_PARAMS}
    )


def test_judge_uses_independent_provider_when_configured():
    s = _override_settings()
    judge = LLMJudge(s, s.judge_model)
    assert judge.llm.provider == "openagents"
    assert judge.llm._client.api_key == "sk-judge"
    assert str(judge.llm._client.base_url).rstrip("/") == _GATEWAY
    assert judge.model_digest == sha256_digest(
        {"provider": "openagents", "model": "glm-5.2", "params": LLMJudge.SCORE_PARAMS}
    )
    # 同模型名走独立 provider 的裁判 digest 必须 ≠ StepFun 路径 digest
    assert judge.model_digest != LLMJudge(_settings(), s.judge_model).model_digest
