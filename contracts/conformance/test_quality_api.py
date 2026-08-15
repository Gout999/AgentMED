"""Quality API v2 写面契约测试（对 contracts/quality-api/openapi.yaml）。

纪律：本文件对「空实现」必须全红（连接错误即失败）——不允许为了跑绿而放水；
只有真实实现了契约的 demo-app 才能让本文件转绿。
"""
import time
import uuid

import pytest

from conftest import BASE_URL

TIMEOUT = 10


def _key() -> str:
    """每次运行唯一幂等键，避免跨次运行互相干扰。"""
    return f"conformance-{uuid.uuid4()}"


def _sample_content(tag: str) -> dict:
    """最小合法 VersionSetContent（digest 为示例值，服务端按 JCS+SHA-256 重算）。"""
    h = uuid.uuid5(uuid.NAMESPACE_URL, f"agentmed-conformance-{tag}").hex * 2
    return {
        "prompt": {
            "prompt_id": "prompts/system.md",
            "version": f"v-test-{tag}",
            "digest": f"sha256:{h}",
        },
        "kb_manifest": {
            "entries": [
                {
                    "kb_id": "products",
                    "entry_id": "x200",
                    "version": "1.0.0",
                    "digest": f"sha256:{h}",
                }
            ],
            "manifest_digest": f"sha256:{h}",
        },
        "model": {
            "provider": "stepfun",
            "model": "step-2-16k",
            "params": {"temperature": 0.0},
            "digest": f"sha256:{h}",
        },
    }


def _create_draft(write_session, tag: str) -> dict:
    r = write_session.post(
        f"{BASE_URL}/v2/versionsets",
        json=_sample_content(tag),
        headers={"Idempotency-Key": _key()},
        timeout=TIMEOUT,
    )
    assert r.status_code == 201, f"创建 VersionSet 应 201，实际 {r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body["status"] == "draft", "新建 VersionSet 初始状态必须为 draft"
    assert "ETag" in r.headers, "创建响应必须带 ETag（CAS 用）"
    assert body["digest"].startswith("sha256:"), "必须返回完整版本 digest"
    return body


def _lifecycle(write_session, vs_id: str, action: str, etag=None, expected_revision=None,
               body_extra=None, idem_key=None):
    body = dict(body_extra or {})
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    headers = {"Idempotency-Key": idem_key or _key()}
    if etag is not None:
        headers["If-Match"] = etag
    return write_session.post(
        f"{BASE_URL}/v2/versionsets/{vs_id}/{action}",
        json=body if body else None,
        headers=headers,
        timeout=TIMEOUT,
    )


def _wait_operation(read_session, operation_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        r = read_session.get(f"{BASE_URL}/v2/operations/{operation_id}", timeout=TIMEOUT)
        assert r.status_code == 200, f"查询 operation 应 200，实际 {r.status_code}"
        last = r.json()
        if last["status"] in ("succeeded", "failed"):
            return last
        time.sleep(0.5)
    pytest.fail(f"operation {operation_id} 在 {timeout_s}s 内未到终态，最后状态: {last}")


def _run_lifecycle_to_terminal(write_session, read_session, vs_id, action, etag=None,
                               expected_revision=None, body_extra=None):
    body_extra = dict(body_extra or {})
    if action == "promote" and "expected_active_digest" not in body_extra:
        active_response = read_session.get(
            f"{BASE_URL}/v2/versionsets",
            params={"status": "active", "limit": 50},
            timeout=TIMEOUT,
        )
        assert active_response.status_code == 200, active_response.text[:300]
        active = active_response.json().get("items") or []
        assert len(active) == 1, f"promote 前必须有唯一 active 基线: {active}"
        body_extra["expected_active_digest"] = active[0]["digest"]
    r = _lifecycle(write_session, vs_id, action, etag, expected_revision, body_extra)
    assert r.status_code == 202, f"{action} 应异步受理 202，实际 {r.status_code}: {r.text[:300]}"
    op = r.json()
    assert op["operation_id"].startswith("op_"), "202 响应必须含 operation_id"
    final = _wait_operation(read_session, op["operation_id"])
    assert final["status"] == "succeeded", f"{action} operation 应 succeeded，实际: {final}"
    return final


def _status(read_session, vs_id: str) -> dict:
    r = read_session.get(f"{BASE_URL}/v2/versionsets/{vs_id}/status", timeout=TIMEOUT)
    assert r.status_code == 200
    return r.json()


# ------------------------------------------------------------ 创建与幂等

def test_create_draft_versionset(write_session):
    body = _create_draft(write_session, "create")
    assert body["revision"] == 1


def test_idempotency_key_replay_returns_same_resource(write_session):
    content = _sample_content("idem-create")
    key = _key()
    r1 = write_session.post(f"{BASE_URL}/v2/versionsets", json=content,
                            headers={"Idempotency-Key": key}, timeout=TIMEOUT)
    r2 = write_session.post(f"{BASE_URL}/v2/versionsets", json=content,
                            headers={"Idempotency-Key": key}, timeout=TIMEOUT)
    assert r1.status_code == 201
    assert r2.status_code in (200, 201), "幂等重放应返回已创建资源"
    assert r2.json()["versionset_id"] == r1.json()["versionset_id"], \
        "相同 Idempotency-Key + 相同内容的重放必须返回同一 VersionSet（不新建）"


def test_idempotency_key_reuse_different_body_rejected(write_session):
    key = _key()
    r1 = write_session.post(f"{BASE_URL}/v2/versionsets", json=_sample_content("a"),
                            headers={"Idempotency-Key": key}, timeout=TIMEOUT)
    assert r1.status_code == 201
    r2 = write_session.post(f"{BASE_URL}/v2/versionsets", json=_sample_content("b"),
                            headers={"Idempotency-Key": key}, timeout=TIMEOUT)
    assert r2.status_code == 422, \
        f"同一 Idempotency-Key 配不同请求体应 422 validation_failed，实际 {r2.status_code}"
    assert r2.json()["error"]["code"] == "validation_failed"


# ------------------------------------------------------------ CAS 纪律

def test_missing_precondition_rejected(write_session):
    """缺 If-Match 且无 expected_revision → 412（或 428）。"""
    vs = _create_draft(write_session, "cas-missing")
    r = write_session.post(
        f"{BASE_URL}/v2/versionsets/{vs['versionset_id']}/stage",
        json={},
        headers={"Idempotency-Key": _key()},
        timeout=TIMEOUT,
    )
    assert r.status_code in (412, 428), \
        f"缺 CAS 前置条件应 412/428，实际 {r.status_code}: {r.text[:300]}"
    if r.status_code == 412:
        assert r.json()["error"]["code"] == "precondition_failed"


def test_stale_revision_conflict(write_session):
    """错误 revision → 409 revision_conflict。"""
    vs = _create_draft(write_session, "cas-stale")
    r = _lifecycle(write_session, vs["versionset_id"], "stage", etag='"999"')
    assert r.status_code == 409, f"过期 revision 应 409，实际 {r.status_code}: {r.text[:300]}"
    err = r.json()["error"]
    assert err["code"] == "revision_conflict"


def test_expected_revision_body_alternative(write_session, read_session):
    """body.expected_revision 是 If-Match 的合法替代（不带 If-Match 头也应受理）。"""
    vs = _create_draft(write_session, "cas-body")
    _run_lifecycle_to_terminal(write_session, read_session, vs["versionset_id"], "stage",
                               expected_revision=vs["revision"])
    assert _status(read_session, vs["versionset_id"])["status"] == "staged"


# ------------------------------------------------------------ 全链路生命周期

def test_full_lifecycle_draft_stage_canary_promote(write_session, read_session):
    vs = _create_draft(write_session, "lifecycle")
    vs_id = vs["versionset_id"]

    st = _status(read_session, vs_id)
    assert st["status"] == "draft"

    _run_lifecycle_to_terminal(write_session, read_session, vs_id, "stage",
                               etag=f'"{vs["revision"]}"')
    st = _status(read_session, vs_id)
    assert st["status"] == "staged", f"stage 后应为 staged，实际 {st['status']}"

    _run_lifecycle_to_terminal(write_session, read_session, vs_id, "canary",
                               expected_revision=st["revision"],
                               body_extra={"percent": 10})
    st = _status(read_session, vs_id)
    assert st["status"] == "canary", f"canary 后应为 canary，实际 {st['status']}"
    assert st.get("canary", {}).get("percent") == 10

    _run_lifecycle_to_terminal(write_session, read_session, vs_id, "promote",
                               expected_revision=st["revision"])
    st = _status(read_session, vs_id)
    assert st["status"] == "active", f"promote 后应为 active，实际 {st['status']}"
    assert st["is_active"] is True
    assert len(st["history"]) >= 3, "迁移历史必须完整（draft→staged→canary→active）"


def test_illegal_transitions_rejected(write_session):
    """非法迁移 → 422 validation_failed（details.subcode=illegal_transition）。"""
    vs = _create_draft(write_session, "illegal")
    vs_id = vs["versionset_id"]
    # draft 直接 canary（跳过 stage）
    r = _lifecycle(write_session, vs_id, "canary", expected_revision=vs["revision"],
                   body_extra={"percent": 10})
    assert r.status_code == 422, f"draft→canary 非法迁移应 422，实际 {r.status_code}"
    assert r.json()["error"]["code"] == "validation_failed"
    # draft 直接 promote
    r = _lifecycle(
        write_session,
        vs_id,
        "promote",
        expected_revision=vs["revision"],
        body_extra={"expected_active_digest": "sha256:" + "0" * 64},
    )
    assert r.status_code == 422, f"draft→promote 非法迁移应 422，实际 {r.status_code}"


def test_rollback_restores_previous(write_session, read_session):
    """v1 promote 后 v2 全量，再 rollback v2 → v1 恢复 active，v2 转 rolled_back。"""
    v1 = _create_draft(write_session, "rb-v1")
    v1_id = v1["versionset_id"]
    _run_lifecycle_to_terminal(write_session, read_session, v1_id, "stage",
                               expected_revision=v1["revision"])
    st = _status(read_session, v1_id)
    _run_lifecycle_to_terminal(write_session, read_session, v1_id, "canary",
                               expected_revision=st["revision"], body_extra={"percent": 10})
    st = _status(read_session, v1_id)
    _run_lifecycle_to_terminal(write_session, read_session, v1_id, "promote",
                               expected_revision=st["revision"])

    v2 = _create_draft(write_session, "rb-v2")
    v2_id = v2["versionset_id"]
    _run_lifecycle_to_terminal(write_session, read_session, v2_id, "stage",
                               expected_revision=v2["revision"])
    st = _status(read_session, v2_id)
    _run_lifecycle_to_terminal(write_session, read_session, v2_id, "canary",
                               expected_revision=st["revision"], body_extra={"percent": 10})
    st = _status(read_session, v2_id)
    _run_lifecycle_to_terminal(write_session, read_session, v2_id, "promote",
                               expected_revision=st["revision"])

    # v1 应已被 supersede
    st1 = _status(read_session, v1_id)
    assert st1["status"] == "superseded" and st1["is_active"] is False

    # 回滚 v2
    st2 = _status(read_session, v2_id)
    _run_lifecycle_to_terminal(write_session, read_session, v2_id, "rollback",
                               expected_revision=st2["revision"],
                               body_extra={"rollback_to": "previous"})
    st2 = _status(read_session, v2_id)
    assert st2["status"] == "rolled_back", f"rollback 后应为 rolled_back，实际 {st2['status']}"
    st1 = _status(read_session, v1_id)
    assert st1["status"] == "active" and st1["is_active"] is True, \
        "rollback to previous 后 v1 必须恢复 active"


# ------------------------------------------------------------ scope 与读面

def test_write_requires_write_scope(read_session):
    """持 read scope 调写面 → 401/403。"""
    r = read_session.post(
        f"{BASE_URL}/v2/versionsets",
        json=_sample_content("scope"),
        headers={"Idempotency-Key": _key()},
        timeout=TIMEOUT,
    )
    assert r.status_code in (401, 403), \
        f"read scope 调写面应 401/403，实际 {r.status_code}（写面仅 Release Controller）"


def test_read_endpoints(read_session):
    """GET /v2/logs 与 GET /v2/feedback 必须可用（分页结构）。"""
    r = read_session.get(f"{BASE_URL}/v2/logs", params={"limit": 10}, timeout=TIMEOUT)
    assert r.status_code == 200, f"GET /v2/logs 应 200，实际 {r.status_code}"
    assert "items" in r.json()
    r = read_session.get(f"{BASE_URL}/v2/feedback", params={"limit": 10}, timeout=TIMEOUT)
    assert r.status_code == 200, f"GET /v2/feedback 应 200，实际 {r.status_code}"
    assert "items" in r.json()


# ------------------------------------------------------------ 故障注入端点

@pytest.mark.parametrize("fault_id", ["B1", "B2", "B3", "B4"])
def test_fault_injection_endpoints(write_session, fault_id):
    """B1–B4 注入端点（x-internal，演示用）。"""
    r = write_session.post(f"{BASE_URL}/admin/inject/{fault_id}", timeout=TIMEOUT)
    assert r.status_code == 200, f"注入 {fault_id} 应 200，实际 {r.status_code}"
    assert r.json()["fault_id"] == fault_id
    r = write_session.post(f"{BASE_URL}/admin/reset", timeout=TIMEOUT)
    assert r.status_code == 200, f"reset 应 200，实际 {r.status_code}"
