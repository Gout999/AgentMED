"""demo-app 端到端集成测试（对运行中的 compose 服务 + 真 PG）。

前置：docker compose up -d postgres demo-app，服务在 ${BASE_URL}。
chat 相关断言聚焦确定性部分（digest 绑定/变更），不依赖 LLM 输出措辞。
"""
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("CASELOOP_QUALITY_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
READ_TOKEN = os.environ.get("CASELOOP_READ_TOKEN", "conformance-read-token")
WRITE_TOKEN = os.environ.get("CASELOOP_WRITE_TOKEN", "conformance-write-token")

TIMEOUT = 15


@pytest.fixture(scope="module")
def write_session():
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {WRITE_TOKEN}"})
    return s


@pytest.fixture(scope="module")
def read_session():
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {READ_TOKEN}"})
    return s


def _key():
    return f"e2e-{uuid.uuid4()}"


def _sample_content(tag):
    h = uuid.uuid5(uuid.NAMESPACE_URL, f"e2e-{tag}").hex * 2
    return {
        "prompt": {"prompt_id": "prompts/system.md", "version": f"v-e2e-{tag}", "digest": f"sha256:{h}"},
        "kb_manifest": {
            "entries": [{"kb_id": "products", "entry_id": "x200", "version": "1.0.0", "digest": f"sha256:{h}"}],
            "manifest_digest": f"sha256:{h}",
        },
        "model": {"provider": "stepfun", "model": "step-3.7-flash", "params": {"temperature": 0.0}, "digest": f"sha256:{h}"},
    }


def _wait_op(read_session, op_id, timeout=20):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = read_session.get(f"{BASE_URL}/v2/operations/{op_id}", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        last = r.json()
        if last["status"] in ("succeeded", "failed"):
            return last
        time.sleep(0.3)
    pytest.fail(f"operation 未终态: {last}")


def _run_lifecycle(write_session, read_session, vs_id, action, etag=None, expected_revision=None, body_extra=None):
    body = dict(body_extra or {})
    if action == "promote" and "expected_active_digest" not in body:
        active_response = read_session.get(
            f"{BASE_URL}/v2/versionsets",
            params={"status": "active", "limit": 50},
            timeout=TIMEOUT,
        )
        assert active_response.status_code == 200, active_response.text[:300]
        active = active_response.json().get("items") or []
        assert len(active) == 1, f"promote 前必须有唯一 active 基线: {active}"
        body["expected_active_digest"] = active[0]["digest"]
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    headers = {"Idempotency-Key": _key()}
    if etag:
        headers["If-Match"] = etag
    r = write_session.post(f"{BASE_URL}/v2/versionsets/{vs_id}/{action}", json=body or None, headers=headers, timeout=TIMEOUT)
    assert r.status_code == 202, f"{action} 应 202: {r.status_code} {r.text[:300]}"
    op = r.json()
    final = _wait_op(read_session, op["operation_id"])
    assert final["status"] == "succeeded", f"{action} 应 succeeded: {final}"
    return final


# ---------------------------------------------------------------- 基线就绪

def test_health_and_baseline(read_session):
    r = read_session.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    assert r.status_code == 200
    r = read_session.get(f"{BASE_URL}/v2/versionsets", params={"limit": 50}, timeout=TIMEOUT)
    assert r.status_code == 200
    vs_list = r.json()["items"]
    baseline = [v for v in vs_list if v["versionset_id"] == "vs_baseline0000000001"]
    assert baseline and baseline[0]["status"] == "active", "基线 VersionSet 必须存在且 active"


# ---------------------------------------------------------------- Quality API 生命周期

def test_lifecycle_full(write_session, read_session):
    r = write_session.post(f"{BASE_URL}/v2/versionsets", json=_sample_content("lc"), headers={"Idempotency-Key": _key()}, timeout=TIMEOUT)
    assert r.status_code == 201
    vs = r.json()
    vs_id = vs["versionset_id"]
    assert vs["status"] == "draft" and vs["revision"] == 1

    _run_lifecycle(write_session, read_session, vs_id, "stage", expected_revision=1)
    st = read_session.get(f"{BASE_URL}/v2/versionsets/{vs_id}/status", timeout=TIMEOUT).json()
    assert st["status"] == "staged"

    _run_lifecycle(write_session, read_session, vs_id, "canary", expected_revision=st["revision"], body_extra={"percent": 10})
    st = read_session.get(f"{BASE_URL}/v2/versionsets/{vs_id}/status", timeout=TIMEOUT).json()
    assert st["status"] == "canary" and st["canary"]["percent"] == 10

    _run_lifecycle(write_session, read_session, vs_id, "promote", expected_revision=st["revision"])
    st = read_session.get(f"{BASE_URL}/v2/versionsets/{vs_id}/status", timeout=TIMEOUT).json()
    assert st["status"] == "active" and st["is_active"] is True


def test_cas_and_scope(read_session, write_session):
    r = write_session.post(f"{BASE_URL}/v2/versionsets", json=_sample_content("cas"), headers={"Idempotency-Key": _key()}, timeout=TIMEOUT)
    vs = r.json()
    # 缺前置
    r = write_session.post(f"{BASE_URL}/v2/versionsets/{vs['versionset_id']}/stage", json={}, headers={"Idempotency-Key": _key()}, timeout=TIMEOUT)
    assert r.status_code in (412, 428)
    # 过期 revision
    r = write_session.post(f"{BASE_URL}/v2/versionsets/{vs['versionset_id']}/stage", json={}, headers={"Idempotency-Key": _key(), "If-Match": '"999"'}, timeout=TIMEOUT)
    assert r.status_code == 409 and r.json()["error"]["code"] == "revision_conflict"
    # read scope 调写面
    r = read_session.post(f"{BASE_URL}/v2/versionsets", json=_sample_content("scope"), headers={"Idempotency-Key": _key()}, timeout=TIMEOUT)
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------- chat / logs / feedback

def test_chat_writes_log(read_session, write_session):
    r = write_session.post(f"{BASE_URL}/chat", json={"message": "X200 蓝牙耳机续航多久？"}, timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["request_id"].startswith("req_")
    assert body["prompt_digest"].startswith("sha256:")

    # 日志落库
    logs = read_session.get(f"{BASE_URL}/v2/logs", params={"limit": 10}, timeout=TIMEOUT).json()["items"]
    assert any(l["request_id"] == body["request_id"] for l in logs), "chat 日志必须落库"

    # 反馈闭环
    fb = write_session.post(f"{BASE_URL}/feedback", json={
        "request_id": body["request_id"],
        "rating": "negative",
        "comment": "回答不对，联系我 13812341234",
        "user_ref": "u_demo",
        "source": "in_app",
    }, timeout=TIMEOUT)
    assert fb.status_code == 201, fb.text[:300]
    fbs = read_session.get(f"{BASE_URL}/v2/feedback", params={"limit": 10}, timeout=TIMEOUT).json()["items"]
    hit = [f for f in fbs if f["feedback_id"] == fb.json()["feedback_id"]]
    assert hit and hit[0]["rating"] == "negative"
    assert "138****1234" in hit[0]["comment"], "反馈评论必须 PII 脱敏"


def test_b1_injection_changes_prompt_digest(read_session, write_session):
    """B1 注入后 live prompt digest 必须偏离基线；reset 恢复。确定性断言。"""

    def chat_prompt_digest():
        r = write_session.post(f"{BASE_URL}/chat", json={"message": "能退货吗？"}, timeout=TIMEOUT)
        assert r.status_code == 200
        return r.json()["prompt_digest"]

    baseline_digest = chat_prompt_digest()

    r = write_session.post(f"{BASE_URL}/admin/inject/B1", timeout=TIMEOUT)
    assert r.status_code == 200 and r.json()["fault_id"] == "B1"
    injected_digest = chat_prompt_digest()
    assert injected_digest != baseline_digest, "B1 注入后 prompt_digest 必须改变"

    r = write_session.post(f"{BASE_URL}/admin/reset", timeout=TIMEOUT)
    assert r.status_code == 200 and "B1" in r.json()["cleared"]
    restored_digest = chat_prompt_digest()
    assert restored_digest == baseline_digest, "reset 后 prompt_digest 必须恢复基线"


def test_b2_injection_changes_kb_digest(read_session, write_session):
    def chat_kb_digest():
        r = write_session.post(f"{BASE_URL}/chat", json={"message": "X200 续航多久？"}, timeout=TIMEOUT)
        assert r.status_code == 200
        return r.json()["kb_manifest_digest"]

    baseline = chat_kb_digest()
    r = write_session.post(f"{BASE_URL}/admin/inject/B2", timeout=TIMEOUT)
    assert r.status_code == 200
    injected = chat_kb_digest()
    assert injected != baseline, "B2 注入后 kb_manifest_digest 必须改变"
    r = write_session.post(f"{BASE_URL}/admin/reset", timeout=TIMEOUT)
    assert r.status_code == 200
