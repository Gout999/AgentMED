"""mcp-eval-runner：probe.freeze 结构校验 + experiment.execute 异步执行链路（S0-006）。

- experiment.execute：工具注册、RUNNING/lease 前置校验、冻结协议与精确 VersionSet 绑定、
  后台线程回流 cells/完整 artifacts、异常回流 cancel。执行机用桩替换，不真跑 LLM。
- probe.freeze：空/错键 probe_set → validation 报错并给结构示例；合法 probe_set 正常冻结。
- experiment.plan：契约对齐——签名不接收 version_refs；版本由 probe.freeze 精确冻结。
"""
from __future__ import annotations

import inspect
import json
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
        if path.endswith("/trials"):
            return {"items": list(self.get_result.get("completed_trials") or [])}
        return self.get_result

    def post(self, path: str, json_body: dict | None = None, **kw) -> dict:
        self.posts.append((path, json_body or {}))
        if path.endswith("/heartbeat"):
            return {
                "lease_id": "lease_000000000001",
                "fencing_token": (json_body or {}).get("fencing_token"),
            }
        if path.endswith("/trials"):
            return {
                "trial": {
                    key: value
                    for key, value in (json_body or {}).items()
                    if key != "fencing_token"
                }
            }
        return {"ok": True}


class FakeQA:
    def __init__(self):
        self.gets: list[tuple[str, dict | None]] = []

    def get(self, path: str, params: dict | None = None, **_kw) -> dict:
        self.gets.append((path, params))
        if path == "/v2/versionsets":
            return {"items": [{"versionset_id": "vs_active", "status": "active"}]}
        return {
            "versionset_id": path.rsplit("/", 1)[-1],
            "status": "active",
            "revision": 7,
            "digest": "sha256:" + "a" * 64,
            "content": {"prompt": {}, "kb_manifest": {}, "model": {}},
        }


def _frozen_payload(**over) -> dict:
    versions = {
        "P0": "sha256:" + "1" * 64,
        "P1": "sha256:" + "2" * 64,
        "K0": "sha256:" + "3" * 64,
        "K1": "sha256:" + "3" * 64,
        "M0": "sha256:" + "4" * 64,
        "M1": "sha256:" + "4" * 64,
    }
    bad = {"versionset_id": "vs_bad00000001", "digest": "sha256:" + "5" * 64, "revision": 1}
    good = {"versionset_id": "vs_good0000001", "digest": "sha256:" + "6" * 64, "revision": 1}
    payload = {
        "case_id": "case_b1",
        "hypothesis_layer": "prompt",
        "probe_set_digest": "sha256:" + "a" * 64,
        "discovery": ["cs-001", "cs-002", "cs-003"],
        "hidden_confirmation": ["cs-004", "cs-005"],
        "unaffected_controls": ["cs-013", "cs-014", "cs-015", "cs-016"],
        "repetitions": 5,
        "versions": versions,
        "cell_versionsets": {"C": bad, "RP": good, "RK": bad, "RM": bad, "G": good},
        "random_seed_ref": "seed://exp_x/1",
        "confidence": 0.95,
        "runner_id": "eval-runner",
        "lease_id": "lease_000000000001",
        "fencing_token": 7,
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

    def run(
        self,
        plan,
        driver,
        *,
        seed=None,
        suppress_digest_capture=False,
        prior_trials=None,
    ):
        assert seed is None or isinstance(seed, int)
        assert suppress_digest_capture is True
        assert isinstance(prior_trials, dict)
        return _StubResult()


class _SlowStubRunner(_StubRunner):
    def run(self, plan, driver, **kwargs):
        time.sleep(1.2)
        return super().run(plan, driver, **kwargs)


# ------------------------------------------------------------------ A. experiment.execute


def test_experiment_execute_tool_registered():
    names = [t.name for t in eval_runner._profiled_mcp("attributionist")._tool_manager.list_tools()]
    assert "experiment.execute" in names
    assert "versionset.list" in names
    assert "versionset.get" in names


def test_versionset_tools_use_quality_read_surface(monkeypatch):
    quality = FakeQA()
    monkeypatch.setattr(eval_runner, "_qa", lambda: quality)

    listed = eval_runner.versionset_list(status="active", limit=999)
    exact = eval_runner.versionset_get("vs_active")

    assert listed["items"][0]["versionset_id"] == "vs_active"
    assert exact["revision"] == 7
    assert exact["content"] == {"prompt": {}, "kb_manifest": {}, "model": {}}
    assert quality.gets == [
        ("/v2/versionsets", {"limit": 200, "status": "active"}),
        ("/v2/versionsets/vs_active", None),
    ]


def test_execute_rejects_state_not_running(monkeypatch):
    fake = FakeCP({"state": "REQUESTED", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.experiment_execute("exp_x")
    assert exc.value.error_code == "VALIDATION_FAILED"
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
    assert eval_runner._wait_for_execution_thread("exp_x") is True


def test_execute_deduplicates_one_in_process_background_worker(monkeypatch):
    started = eval_runner.threading.Event()
    release = eval_runner.threading.Event()
    calls: list[str] = []

    def blocked_bg(experiment_id: str):
        calls.append(experiment_id)
        started.set()
        assert release.wait(2.0)

    monkeypatch.setattr(eval_runner, "_execute_experiment_background", blocked_bg)
    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)

    first = eval_runner.experiment_execute("exp_x")
    assert started.wait(2.0)
    second = eval_runner.experiment_execute("exp_x")

    assert first == second == {"status": "executing", "experiment_id": "exp_x"}
    assert calls == ["exp_x"]
    release.set()
    assert eval_runner._wait_for_execution_thread("exp_x") is True


def test_execute_not_found_propagates(monkeypatch):
    from common.errors import not_found

    class _Fake:
        def get(self, path: str, **kw):
            raise not_found(f"experiment nope not found")

    monkeypatch.setattr(eval_runner, "_cp", lambda: _Fake())
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.experiment_execute("exp_nope")
    assert exc.value.error_code == "NOT_FOUND"


def test_background_posts_cells_and_verdict(monkeypatch, tmp_path):
    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(eval_runner, "_build_execution_context", lambda eid, payload: (_plan(), None, None))
    monkeypatch.setattr(eval_runner, "QualityAPIClient", lambda settings: object())
    monkeypatch.setattr(eval_runner, "ImmutableVersionSetDriver", lambda refs: object())
    monkeypatch.setattr(eval_runner, "ExperimentRunner", _StubRunner)
    monkeypatch.setattr(
        eval_runner,
        "_settings",
        lambda: eval_runner.Settings(experiment_evidence_dir=str(tmp_path)),
    )

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
    assert verdict_body == {
        "fencing_token": 7,
        "evidence_bundle": _StubResult.bundle,
        "attribution_report": _StubResult.report,
    }
    assert all(body["fencing_token"] == 7 for _, body in cells)
    heartbeats = [(p, b) for p, b in fake.posts if p.endswith("/heartbeat")]
    assert heartbeats == [
        (
            "/v1/cases/case_b1/heartbeat",
            {"worker_id": "eval-runner", "fencing_token": 7},
        )
    ]


def test_prior_trial_map_is_exact_and_rejects_duplicate_keys():
    item = {
        "cell": "C",
        "probe_id": "cs-001",
        "repetition": 1,
        "recovered": False,
        "output_ref": "data:application/json;base64,e30=",
        "output_digest": "sha256:" + "a" * 64,
    }

    mapped = eval_runner._prior_trial_map([item])

    assert set(mapped) == {("C", "cs-001", 1)}
    assert mapped[("C", "cs-001", 1)].output_ref == item["output_ref"]
    with pytest.raises(RuntimeError, match="duplicate completed trial"):
        eval_runner._prior_trial_map([item, item])


def test_background_resumes_authoritative_trials_and_checkpoints_only_missing(
    monkeypatch, tmp_path
):
    prior = {
        "cell": "C",
        "probe_id": "cs-001",
        "repetition": 1,
        "recovered": False,
        "output_ref": "data:application/json;base64,e30=",
        "output_digest": "sha256:" + "a" * 64,
    }
    fake = FakeCP(
        {
            "state": "RUNNING",
            "payload": _frozen_payload(),
            "completed_trials": [prior],
        }
    )
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(
        eval_runner,
        "_build_execution_context",
        lambda eid, payload: (_plan(), None, None),
    )
    monkeypatch.setattr(eval_runner, "QualityAPIClient", lambda settings: object())
    monkeypatch.setattr(eval_runner, "ImmutableVersionSetDriver", lambda refs: object())
    monkeypatch.setattr(
        eval_runner,
        "_settings",
        lambda: eval_runner.Settings(experiment_evidence_dir=str(tmp_path)),
    )

    class ResumeRunner:
        def __init__(self, *_args, artifact_dir, trial_callback, **_kwargs):
            self.artifact_dir = artifact_dir
            self.trial_callback = trial_callback

        def run(self, plan, _driver, *, prior_trials, **_kwargs):
            assert set(prior_trials) == {("C", "cs-001", 1)}
            raw = {
                "experiment_id": "exp_x",
                "case_id": "case_b1",
                "arm": "C",
                "probe_id": "cs-002",
                "repetition": 1,
                "recovered": False,
            }
            path = self.artifact_dir / "exp_x" / "probe-outputs" / "C" / "cs-002-rep1.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(raw), encoding="utf-8")
            run = eval_runner.ProbeRun(
                probe_id="cs-002",
                repetition=1,
                recovered=False,
                output_ref=path.resolve().as_uri(),
                output_digest=eval_runner.sha256_digest(raw),
                answer="",
            )
            checkpointed = self.trial_callback("C", run)
            assert checkpointed.output_ref.startswith("data:application/json;base64,")
            return _StubResult()

    monkeypatch.setattr(eval_runner, "ExperimentRunner", ResumeRunner)

    eval_runner._execute_experiment_background("exp_x")

    trial_posts = [(path, body) for path, body in fake.posts if path.endswith("/trials")]
    assert len(trial_posts) == 1
    assert trial_posts[0][1]["probe_id"] == "cs-002"
    assert all(body.get("probe_id") != "cs-001" for _, body in trial_posts)


def test_background_renews_lease_while_evaluator_is_running(monkeypatch, tmp_path):
    """A slow provider call must not leave the authoritative Case lease stale."""

    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(
        eval_runner,
        "_build_execution_context",
        lambda eid, payload: (_plan(), None, None),
    )
    monkeypatch.setattr(eval_runner, "QualityAPIClient", lambda settings: object())
    monkeypatch.setattr(eval_runner, "ImmutableVersionSetDriver", lambda refs: object())
    monkeypatch.setattr(eval_runner, "ExperimentRunner", _SlowStubRunner)
    monkeypatch.setattr(
        eval_runner,
        "_settings",
        lambda: eval_runner.Settings(
            experiment_evidence_dir=str(tmp_path),
            experiment_heartbeat_interval_seconds=1,
        ),
    )

    eval_runner._execute_experiment_background("exp_x")

    heartbeats = [(path, body) for path, body in fake.posts if path.endswith("/heartbeat")]
    assert len(heartbeats) >= 2
    assert all(
        body == {"worker_id": "eval-runner", "fencing_token": 7}
        for _, body in heartbeats
    )
    assert len([(path, body) for path, body in fake.posts if path.endswith("/verdict")]) == 1


def test_experiment_run_posts_exact_lease_and_fencing(monkeypatch):
    """只有显式 experiment.run 可携带 Case lease 启动归因。"""
    fake = FakeCP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(
        eval_runner,
        "_settings",
        lambda: eval_runner.Settings(mcp_worker_id="eval-runner"),
    )
    eval_runner.experiment_run("exp_x", "lease_exact0001", 42)
    assert fake.posts == [
        (
            "/v1/experiments/exp_x/start",
            {"runner_id": "eval-runner", "lease_id": "lease_exact0001", "fencing_token": 42},
        )
    ]


def test_background_failure_posts_cancel(monkeypatch, tmp_path):
    fake = FakeCP({"state": "RUNNING", "payload": _frozen_payload()})
    monkeypatch.setattr(eval_runner, "_cp", lambda: fake)
    monkeypatch.setattr(eval_runner, "_build_execution_context", lambda eid, payload: (_plan(), None, None))
    monkeypatch.setattr(
        eval_runner,
        "_settings",
        lambda: eval_runner.Settings(experiment_evidence_dir=str(tmp_path)),
    )

    def boom(*_a, **_k):
        raise RuntimeError("demo-app down")

    monkeypatch.setattr(eval_runner, "QualityAPIClient", boom)

    eval_runner._execute_experiment_background("exp_x")

    cancels = [(p, b) for p, b in fake.posts if p.endswith("/cancel")]
    assert len(cancels) == 1
    assert "demo-app down" in cancels[0][1]["reason"]
    assert cancels[0][1]["runner_id"] == "eval-runner"
    assert cancels[0][1]["lease_id"] == "lease_000000000001"
    assert cancels[0][1]["fencing_token"] == 7


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
    frozen = _frozen_payload()
    result = eval_runner.probe_freeze(
        "exp_x",
        {
            "discovery": ["cs-001"],
            "hidden_confirmation": ["cs-004"],
            "unaffected_controls": ["cs-013"],
            "repetitions": 5,
            "versions": frozen["versions"],
            "cell_versionsets": frozen["cell_versionsets"],
            "random_seed_ref": "seed://exp_x/1",
            "confidence": 0.95,
        },
    )
    assert result["probe_set_digest"].startswith("sha256:")
    assert fake.posts[0][0] == "/v1/experiments/exp_x/protocol"
    assert fake.posts[0][1]["discovery"] == ["cs-001"]
    assert fake.posts[0][1]["execution_profile"] == "live"
    assert set(fake.posts[0][1]["cell_versionsets"]) == {"C", "RP", "RK", "RM", "G"}


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
