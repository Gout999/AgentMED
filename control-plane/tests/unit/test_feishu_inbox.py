"""Feishu complaint filing binds the original message before Case creation."""
from __future__ import annotations

import hashlib

import httpx

from app.notifications.adapters import FeishuLiveAdapter
from app.models.tables import Aggregate, Inbox
from app.services.b1_fixture import load_b1_complaint_fixture
from tests.conftest import TEST_CONTROL_TOKEN


def _adapter(monkeypatch, inbound, *, base_url: str = "https://open.feishu.cn"):
    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        base_url=base_url,
        client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(500))),
    )
    monkeypatch.setattr(adapter, "fetch_text_message", lambda _message_id: dict(inbound))
    return adapter


def _inbound(
    message_id: str = "om_original_001",
    *,
    text: str = "昨天仲可以退貨，今日點解唔得？",
    create_time: str = "1786212345000",
):
    return {
        "provider": "feishu",
        "provider_origin": "https://open.feishu.cn",
        "message_id": message_id,
        "channel": "feishu:oc_chat_001",
        "thread_ref": f"feishu:oc_chat_001:{message_id}",
        "text": text,
        "text_digest": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "sender_ref": "feishu-sender:sha256:private",
        "create_time": create_time,
    }


def _seed_b1_injection(client, *, injected_at: str = "2026-08-08T18:00:00+00:00"):
    with client.app.state.session_factory() as session:
        session.add(
            Aggregate(
                aggregate_type="demo_fault_injection",
                aggregate_id="inject-feishu-b1-exact",
                state="COMPLETED",
                revision=2,
                payload={
                    "fault_id": "B1",
                    "fault_versionset_id": "vs_b1fault000000000001",
                    "receipt": {
                        "fault_id": "B1",
                        "fault_versionset_id": "vs_b1fault000000000001",
                        "injected_at": injected_at,
                    },
                },
            )
        )
        session.commit()


def test_feishu_complaint_files_once_and_replays_exact_case(app_client, monkeypatch):
    client, _quality = app_client
    client.app.state.notification_adapter = _adapter(monkeypatch, _inbound())
    headers = {"Authorization": f"Bearer {TEST_CONTROL_TOKEN}"}
    path = "/v1/inbox/feishu/messages/om_original_001/complaint"

    first = client.post(path, json={"app_ref": "demo-app:b1-live"}, headers=headers)
    retry = client.post(path, json={"app_ref": "demo-app:b1-live"}, headers=headers)

    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert first.json()["duplicate"] is False
    assert retry.json()["duplicate"] is True
    assert retry.json()["case_id"] == first.json()["case_id"]
    assert first.json()["inbound"]["message_id"] == "om_original_001"
    case = client.get(f"/v1/cases/{first.json()['case_id']}").json()
    assert case["payload"]["thread_ref"] == "feishu:oc_chat_001:om_original_001"


def test_feishu_complaint_seals_exact_completed_injection_into_case(app_client, monkeypatch):
    client, _quality = app_client
    fixture = load_b1_complaint_fixture()
    inbound = _inbound(text=fixture.text)
    client.app.state.notification_adapter = _adapter(monkeypatch, inbound)
    _seed_b1_injection(client)

    path = "/v1/inbox/feishu/messages/om_original_001/complaint"
    body = {"demo_fault_injection_id": "inject-feishu-b1-exact"}
    first = client.post(path, json=body)
    retry = client.post(path, json=body)

    assert first.status_code == 200, first.text
    assert retry.status_code == 200, retry.text
    assert first.json()["demo_fault_injection_id"] == "inject-feishu-b1-exact"
    assert retry.json()["demo_fault_injection_id"] == "inject-feishu-b1-exact"
    case = client.get(f"/v1/cases/{first.json()['case_id']}").json()
    assert case["payload"]["demo_fault_injection_id"] == "inject-feishu-b1-exact"
    assert case["payload"]["provider_origin"] == "https://open.feishu.cn"
    assert case["payload"]["provider_create_time"] == inbound["create_time"]
    assert case["payload"]["source_text_digest"] == fixture.text_digest
    with client.app.state.session_factory() as session:
        inbox = session.query(Inbox).one()
        assert inbox.raw_payload["provider_origin"] == "https://open.feishu.cn"
        assert inbox.raw_payload["provider_create_time"] == inbound["create_time"]
        assert inbox.raw_payload["source_text_digest"] == fixture.text_digest

    mismatch = client.post(
        path,
        json={"demo_fault_injection_id": "inject-feishu-b1-other"},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "demo_fault_injection_binding_invalid"


def test_b1_feishu_complaint_rejects_nonfixture_text_before_case_creation(
    app_client, monkeypatch
):
    client, _quality = app_client
    client.app.state.notification_adapter = _adapter(monkeypatch, _inbound(text="hello"))
    _seed_b1_injection(client)

    response = client.post(
        "/v1/inbox/feishu/messages/om_original_001/complaint",
        json={"demo_fault_injection_id": "inject-feishu-b1-exact"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "b1_complaint_fixture_mismatch"
    assert client.get("/v1/cases").json()["items"] == []
    with client.app.state.session_factory() as session:
        assert session.query(Inbox).count() == 0


def test_b1_feishu_complaint_rejects_nonofficial_provider_origin_before_case_creation(
    app_client, monkeypatch
):
    client, _quality = app_client
    fixture = load_b1_complaint_fixture()
    inbound = _inbound(text=fixture.text)
    inbound["provider_origin"] = "http://127.0.0.1:9999"
    client.app.state.notification_adapter = _adapter(
        monkeypatch,
        inbound,
        base_url="http://127.0.0.1:9999",
    )
    _seed_b1_injection(client)

    response = client.post(
        "/v1/inbox/feishu/messages/om_original_001/complaint",
        json={"demo_fault_injection_id": "inject-feishu-b1-exact"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "b1_feishu_provider_origin_invalid"
    assert client.get("/v1/cases").json()["items"] == []
    with client.app.state.session_factory() as session:
        assert session.query(Inbox).count() == 0


def test_b1_feishu_complaint_rejects_preinjection_message_before_case_creation(
    app_client, monkeypatch
):
    client, _quality = app_client
    fixture = load_b1_complaint_fixture()
    client.app.state.notification_adapter = _adapter(
        monkeypatch,
        _inbound(text=fixture.text, create_time="1786212345000"),
    )
    _seed_b1_injection(client, injected_at="2026-08-08T19:00:00+00:00")

    response = client.post(
        "/v1/inbox/feishu/messages/om_original_001/complaint",
        json={"demo_fault_injection_id": "inject-feishu-b1-exact"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "b1_complaint_chronology_invalid"
    assert client.get("/v1/cases").json()["items"] == []
    with client.app.state.session_factory() as session:
        assert session.query(Inbox).count() == 0


def test_feishu_complaint_rejects_unknown_injection_before_case_creation(app_client, monkeypatch):
    client, _quality = app_client
    client.app.state.notification_adapter = _adapter(monkeypatch, _inbound())
    response = client.post(
        "/v1/inbox/feishu/messages/om_original_001/complaint",
        json={"demo_fault_injection_id": "inject-missing-b1"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "demo_fault_injection_binding_invalid"
    assert client.get("/v1/cases").json()["items"] == []


def test_feishu_complaint_rejects_adapter_identity_substitution(app_client, monkeypatch):
    client, _quality = app_client
    client.app.state.notification_adapter = _adapter(monkeypatch, _inbound("om_other"))
    response = client.post(
        "/v1/inbox/feishu/messages/om_original_001/complaint",
        json={},
        headers={"Authorization": f"Bearer {TEST_CONTROL_TOKEN}"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "feishu_message_binding_invalid"
    assert client.get("/v1/cases").json()["items"] == []


def test_feishu_complaint_audit_failure_rolls_back_case(app_client, monkeypatch):
    client, _quality = app_client
    client.app.state.notification_adapter = _adapter(monkeypatch, _inbound())
    client.app.state.settings.audit_force_fail = True
    response = client.post(
        "/v1/inbox/feishu/messages/om_original_001/complaint",
        json={},
        headers={"Authorization": f"Bearer {TEST_CONTROL_TOKEN}"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "audit_unavailable"
    client.app.state.settings.audit_force_fail = False
    assert client.get("/v1/cases").json()["items"] == []
