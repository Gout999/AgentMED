"""mcp-eval-runner：probe.freeze 结构校验 + experiment.execute 异步执行链路（S0-006）。

- experiment.execute：工具注册、状态前置校验、冻结协议三探针集非空校验、后台线程回流
  cells/verdict、异常回流 cancel。执行机用桩替换，不真跑 LLM。
- probe.freeze：空/错键 probe_set → validation 报错并给结构示例；合法 probe_set 正常冻结。
- experiment.plan：契约对齐——签名不再接收 version_refs（版本由 execute 现场捕获）。
"""
from __future__ import annotations

import inspect
import os
import time

import pytest

from servers import eval_runner

# 探针集键名（与实现保持一致）。
_REQUIRED = ("discovery", "hidden_confirmation", "unaffected_controls")


class FakeCP:
    """控制面假客户端：脚本化 GET 结果，记录所有 POST/GET。"""

    def __init__(self, get_result: dict | None = None):
        self.get_result = get_result or {}
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def get(self, path: str, **kw) -> dict:
        self.gets.append(path)
        return self.get_result

    def post(self, path: str, json_body: dict | None = None, **kw) -> dict:
        self.posts.append((path, json_body or {}))
        return {"ok": True}


def _frozen_payload(**over) -> dict:
    payload = {
        "case_id": "case_b1",
        "hypothesis_layer": "prompt",
        "probe_set_digest": "sha256:" + "a" * 64,
        "discovery": ["cs-001", "cs-002", "cs-003"],
        "hidden_confirmation": ["cs-004", "cs-005"],
        "unaffected_controls": ["cs-013", "cs-014", "cs-015", "cs-016"],
        "repetitions": 5,
        "versions": {},
        "random_seed_ref": "seed://exp_x/1",
    }
    payload.update(over)
    return payload


def _plan():
    from eval_harness.models import ExperimentPlan

    return ExperimentPlan(
        experiment_id="exp_x",
        case_id="case_b1",
        matrix="five_cell",
        repetitions=1,
        confidence=0.95,
        delta_min=0.2,
        probe_set_digest="sha256:" + "a" * 64,
        version_digests={},
        discovery=["cs-001"],
        hidden_confirmation=["cs-004"],
        unaffected_controls=["cs-013"],
    )


class _StubCell:
    def __init__(self):
        self.runs = []


class _StubResult:
    bundle = {
        "bundle_id": "eb_stub",
        "protocol": {
            "random_arm_order": [
                "RP@cs-001",
                "G@cs-001",
                "RK@cs-001",
                "C@cs-001",
                "RM@cs-001",
            ],
        },
    }
    cells = {arm: _StubCell() for arm in ("C", "RP", "RK", "RM", "G")}
    verdict = {"decision": "ATTRIBUTED", "attributed_layer": "prompt"}
    report = {
        "report_id": "attr_stub",
        "deltas": {
            "prompt": {"estimate": 1.0},
            "kb": {"estimate": 0.0},
            "model_params": {"estimate": 0.0},
        },
    }


class _StubRunner:
    def __init__(self, *_a, **_k):
        pass

    def run(self, plan, driver):
        return _StubResult()


# ------------------------------------------------------------------ A. experiment.execute


def test_experiment_execute_tool_registered():
    names = [t.name for t in eval_runner.mcp._tool_manager.list_tools()]
    assert "experiment.execute" in names


def test_execute_rejects_state_not_frozen_or_running(monkeypatch):
    fake = FakeCP({"state": "REQUESTED", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.experiment_execute("exp_x")
    assert exc.value.error_code == "VALIDATION_FAILED"
    assert "PROTOCOL_FROZEN" in exc.value.message
    assert "RUNNING" in exc.value.message


def test_execute_rejects_empty_frozen_probe_sets(monkeypatch):
    payload = _frozen_payload(discovery=[], unaffected_controls=[])
    fake = FakeCP({"state": "RUNNING", "payload": payload})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.experiment_execute("exp_x")
    assert "discovery" in exc.value.message
    assert "unaffected_controls" in exc.value.message
    assert "cs-001" in exc.value.message  # 操作手册：给出正确结构示例


def test_execute_returns_executing_and_spawns_background(monkeypatch):
    spawned: list[str] = []

    def fake_bg(experiment_id: str):
        spawned.append(experiment_id)

    monkeypatch.setattr(eval_runner, "_execute_experiment_background", fake_bg)
    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)

    result = eval_runner.experiment_execute("exp_x")
    assert result == {"status": "executing", "experiment_id": "exp_x"}

    # 后台线程异步调用：轮询等待线程把 fake_bg 跑起来。
    deadline = time.monotonic() + 2.0
    while not spawned and time.monotonic() < deadline:
        time.sleep(0.02)
    assert spawned == ["exp_x"]


def test_execute_not_found_propagates(monkeypatch):
    from common.errors import not_found

    class _Fake:
        def get(self, path: str, **kw):
            raise not_found(f"experiment nope not found")

    monkeypatch.setattr(eval_runner, "_cp", lambda: _Fake())
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.experiment_execute("exp_nope")
    assert exc.value.error_code == "NOT_FOUND"


def test_background_posts_cells_and_verdict(monkeypatch):
    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(eval_runner, "_build_execution_context", lambda eid, payload: (_plan(), None, None))
    monkeypatch.setattr(eval_runner, "QualityAPIClient", lambda settings: object())
    monkeypatch.setattr(eval_runner, "DemoAppB1Driver", lambda client: object())
    monkeypatch.setattr(eval_runner, "ExperimentRunner", _StubRunner)

    eval_runner._execute_experiment_background("exp_x")

    cells = [(p, b) for p, b in fake.posts if p.endswith("/cells")]
    assert len(cells) == 5, f"应回流 5 个 cell，实际 {len(cells)}"
    arms = [b["cell"] for _, b in cells]
    assert set(arms) == {"C", "RP", "RK", "RM", "G"}
    assert all(b["arm_order_index"] in range(5) for _, b in cells)
    assert all(0.0 <= b["recovery_rate"] <= 1.0 for _, b in cells)

    verdicts = [(p, b) for p, b in fake.posts if p.endswith("/verdict")]
    assert len(verdicts) == 1
    verdict_body = verdicts[0][1]
    assert verdict_body["verdict"] == "ATTRIBUTED"
    assert verdict_body["attributed_layer"] == "prompt"
    assert set(verdict_body["deltas"]) == {"prompt", "kb", "model_params"}


def test_background_progresses_state_if_not_running(monkeypatch):
    """execute 允许 PROTOCOL_FROZEN：后台线程先推进 /start 再执行。"""
    fake = FakeCP({"state": "PROTOCOL_FROZEN", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(eval_runner, "_build_execution_context", lambda eid, payload: (_plan(), None, None))
    monkeypatch.setattr(eval_runner, "QualityAPIClient", lambda settings: object())
    monkeypatch.setattr(eval_runner, "DemoAppB1Driver", lambda client: object())
    monkeypatch.setattr(eval_runner, "ExperimentRunner", _StubRunner)

    eval_runner._execute_experiment_background("exp_x")

    started = [(p, b) for p, b in fake.posts if p.endswith("/start")]
    assert len(started) == 1
    assert started[0][1]["runner_id"] == "eval-runner"


def test_background_failure_posts_cancel(monkeypatch):
    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(eval_runner, "_build_execution_context", lambda eid, payload: (_plan(), None, None))

    def boom(*_a, **_k):
        raise RuntimeError("demo-app down")

    monkeypatch.setattr(eval_runner, "QualityAPIClient", boom)

    eval_runner._execute_experiment_background("exp_x")

    cancels = [(p, b) for p, b in fake.posts if p.endswith("/cancel")]
    assert len(cancels) == 1
    assert "demo-app down" in cancels[0][1]["reason"]


# ------------------------------------------------------------------ B. probe.freeze 结构校验


def test_probe_freeze_rejects_wrong_structure(monkeypatch):
    fake = FakeCP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    # 错误姿势：顶层多套一层（e2e 实战里归因师犯的错）。
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.probe_freeze(
            "exp_x",
            {"probe_set": {"discovery": ["cs-001"], "hidden_confirmation": ["cs-004"]}, "unaffected_controls": []},
        )
    assert exc.value.error_code == "VALIDATION_FAILED"
    for key in _REQUIRED:
        assert key in exc.value.message
    assert "cs-001" in exc.value.message  # 结构示例教正确键名


def test_probe_freeze_rejects_empty_arrays(monkeypatch):
    fake = FakeCP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.probe_freeze(
            "exp_x",
            {"discovery": [], "hidden_confirmation": ["cs-004"], "unaffected_controls": ["cs-013"]},
        )
    assert "discovery 为空数组" in exc.value.message


def test_probe_freeze_valid_posts_protocol(monkeypatch):
    fake = FakeCP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    result = eval_runner.probe_freeze(
        "exp_x",
        {
            "discovery": ["cs-001"],
            "hidden_confirmation": ["cs-004"],
            "unaffected_controls": ["cs-013"],
            "repetitions": 5,
        },
    )
    assert result["probe_set_digest"].startswith("sha256:")
    assert fake.posts[0][0] == "/v1/experiments/exp_x/protocol"
    assert fake.posts[0][1]["discovery"] == ["cs-001"]


# ------------------------------------------------------------------ B. experiment.plan 契约对齐


def test_plan_signature_drops_version_refs():
    params = list(inspect.signature(eval_runner.experiment_plan).parameters)
    assert "case_id" in params
    assert "matrix" in params
    assert "version_refs" not in params


# ------------------------------------------------------------------ 真机慢冒烟（默认跳过）


@pytest.mark.slow
def test_execute_live_smoke_real_control_plane():
    """真机冒烟（默认跳过）：连真实 control-plane，execute 对不存在实验抛 McpError。

    前置：CASELOOP_LIVE_TESTS=1 且 control-plane 可达。零污染（不创建实验）。
    说明：control-plane 的 FastAPI 错误走 {"detail": {...}} 信封，common.http._map_error
    提取 code 失败时降级为 VALIDATION_FAILED，故此处只断言「抛 McpError」，不绑定具体码
    （该 detail 兼容局限为既有行为，非本任务引入）。
    """
    if os.environ.get("CASELOOP_LIVE_TESTS") != "1":
        pytest.skip("CASELOOP_LIVE_TESTS != 1：默认跳过真机冒烟")
    with pytest.raises(eval_runner.McpError):
        eval_runner.experiment_execute("exp_live_smoke_does_not_exist")
