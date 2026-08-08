"""ChangeSet 状态机服务单元测试。"""
import pytest

from app.config import Settings
from app.quality.client import FakeQualityClient
from app.services.changeset_service import ChangeSetService, ChangeSetServiceError
from app.services.release_service import ReleaseService
from tests.conftest import (
    make_action_approval,
    make_approval,
    make_workorder,
    register_gate_for_workorder,
)


def _svc(session) -> ChangeSetService:
    return ChangeSetService(session, Settings())


def _prepared(session, seed: int):
    quality = FakeQualityClient()
    quality.seed_versionset(
        "vs_demo001fixedversionset01", status="draft", revision=1, digest="sha256:" + "b" * 64
    )
    release = ReleaseService(session, quality, Settings())
    wo = make_workorder(
        workorder_id=f"wo_{seed:012d}",
        nonce=f"00000000-0000-0000-0000-{seed:012d}",
        case_id="case_x",
    )
    report = register_gate_for_workorder(release, wo)
    release.register_workorder(wo)
    session.flush()
    return _svc(session), release, wo, report, f"cs_{wo['workorder_id']}"


def test_changeset_approval_flow(sqlite_session):
    svc, release, wo, report, cs_id = _prepared(sqlite_session, 1)
    assert svc.get(cs_id)["state"] == "DRAFTED"

    r = svc.attach_gate(cs_id, eval_id=report["eval_id"], report_hash=wo["gate_report_ref"]["digest"].removeprefix("sha256:"))
    assert r["state"] == "GATE_ATTACHED"

    r = svc.request_approval(cs_id, workorder_hash=wo["hash"], nonce=wo["nonce"], expiry=wo["expiry"])
    assert r["state"] == "AWAITING_APPROVAL"

    approval = make_approval(wo, "ap_1")
    release.grant_approval(approval)
    assert svc.get(cs_id)["state"] == "APPROVED"

    release.start_release(
        workorder_id=wo["workorder_id"],
        approval_id=approval["approval_id"],
        versionset_id="vs_demo001fixedversionset01",
        release_id="rel_1",
    )
    assert svc.get(cs_id)["state"] == "COMMITTED"


def test_changeset_reject_flow(sqlite_session):
    svc, release, wo, report, cs_id = _prepared(sqlite_session, 2)
    svc.attach_gate(cs_id, eval_id=report["eval_id"], report_hash=wo["gate_report_ref"]["digest"].removeprefix("sha256:"))
    svc.request_approval(cs_id, workorder_hash=wo["hash"], nonce=wo["nonce"], expiry=wo["expiry"])
    approval = make_approval(wo, "ap_2")
    approval["decision"] = "rejected"
    release.grant_approval(approval)
    r = svc.reject(cs_id, approval_id="ap_2", approver="human-1", reason="no")
    assert r["state"] == "REJECTED"


def test_changeset_expire_from_approved(sqlite_session):
    svc, release, wo, report, cs_id = _prepared(sqlite_session, 3)
    svc.attach_gate(cs_id, eval_id=report["eval_id"], report_hash=wo["gate_report_ref"]["digest"].removeprefix("sha256:"))
    svc.request_approval(cs_id, workorder_hash=wo["hash"], nonce=wo["nonce"], expiry=wo["expiry"])
    release.grant_approval(make_approval(wo, "ap_3"))
    r = svc.expire(cs_id, workorder_hash=wo["hash"], expiry=wo["expiry"])
    assert r["state"] == "EXPIRED"


def test_changeset_supersede(sqlite_session):
    svc = _svc(sqlite_session)
    cs_id = svc.create(case_id="case_x", workorder_ref="wo_4", workorder_hash="h" * 64, channel="prompt", author_agent="r")["changeset_id"]
    r = svc.supersede(cs_id, replaced_by="cs_new")
    assert r["state"] == "SUPERSEDED"


def test_illegal_transition(sqlite_session):
    svc = _svc(sqlite_session)
    cs_id = svc.create(case_id="case_x", workorder_ref="wo_5", workorder_hash="h" * 64, channel="prompt", author_agent="r")["changeset_id"]
    with pytest.raises(ChangeSetServiceError):
        svc.approve(cs_id, approval_id="ap_x", approver="human-1", workorder_hash="h" * 64)  # DRAFTED 不能直接 approve


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nonce", "00000000-0000-0000-0000-999999999999"),
        ("expiry", "2098-01-01T00:00:00+00:00"),
    ],
)
def test_approval_request_must_copy_workorder_window(sqlite_session, field, value):
    svc, _release, wo, report, cs_id = _prepared(sqlite_session, 10 if field == "nonce" else 11)
    svc.attach_gate(
        cs_id,
        eval_id=report["eval_id"],
        report_hash=wo["gate_report_ref"]["digest"].removeprefix("sha256:"),
    )
    request = {
        "workorder_hash": wo["hash"],
        "nonce": wo["nonce"],
        "expiry": wo["expiry"],
    }
    request[field] = value

    with pytest.raises(ChangeSetServiceError) as exc:
        svc.request_approval(cs_id, **request)

    assert exc.value.code == "hash_mismatch"
    assert svc.get(cs_id)["state"] == "GATE_ATTACHED"


def test_changeset_cannot_use_another_workorder_approval(sqlite_session):
    svc_a, release_a, wo_a, report_a, cs_a = _prepared(sqlite_session, 12)
    svc_a.attach_gate(
        cs_a,
        eval_id=report_a["eval_id"],
        report_hash=wo_a["gate_report_ref"]["digest"].removeprefix("sha256:"),
    )
    svc_a.request_approval(
        cs_a,
        workorder_hash=wo_a["hash"],
        nonce=wo_a["nonce"],
        expiry=wo_a["expiry"],
    )
    release_a.grant_approval(make_approval(wo_a, "ap_cross_a"))

    svc_b, _release_b, wo_b, report_b, cs_b = _prepared(sqlite_session, 13)
    svc_b.attach_gate(
        cs_b,
        eval_id=report_b["eval_id"],
        report_hash=wo_b["gate_report_ref"]["digest"].removeprefix("sha256:"),
    )
    svc_b.request_approval(
        cs_b,
        workorder_hash=wo_b["hash"],
        nonce=wo_b["nonce"],
        expiry=wo_b["expiry"],
    )

    with pytest.raises(ChangeSetServiceError) as exc:
        svc_b.approve(
            cs_b,
            approval_id="ap_cross_a",
            approver="human-1",
            workorder_hash=wo_b["hash"],
        )

    assert exc.value.code == "hash_mismatch"
    assert svc_b.get(cs_b)["state"] == "AWAITING_APPROVAL"


def test_changeset_commit_requires_bound_release(sqlite_session):
    svc, release, wo, report, cs_id = _prepared(sqlite_session, 14)
    svc.attach_gate(
        cs_id,
        eval_id=report["eval_id"],
        report_hash=wo["gate_report_ref"]["digest"].removeprefix("sha256:"),
    )
    svc.request_approval(
        cs_id,
        workorder_hash=wo["hash"],
        nonce=wo["nonce"],
        expiry=wo["expiry"],
    )
    release.grant_approval(make_approval(wo, "ap_commit_bound"))

    with pytest.raises(ChangeSetServiceError) as exc:
        svc.commit(cs_id, release_id="rel_unbound")

    assert exc.value.code == "hash_mismatch"
    assert svc.get(cs_id)["state"] == "APPROVED"


def test_action_grant_cannot_approve_a_changeset(sqlite_session):
    _svc_a, release_a, wo_a, _report_a, _cs_a = _prepared(sqlite_session, 15)
    initial = make_approval(wo_a, "ap_action_source_initial")
    release_a.grant_approval(initial)
    started = release_a.start_release(
        workorder_id=wo_a["workorder_id"],
        approval_id=initial["approval_id"],
        versionset_id="vs_demo001fixedversionset01",
    )
    release_a.stage(started["release_id"], idempotency_key="idem-action-source-stage")
    aggregate = release_a.store.get_aggregate("release", started["release_id"])
    context = release_a._expected_action_context(aggregate, "canary")
    action_grant = make_action_approval(
        wo_a,
        approval_id="ap_action_source_canary",
        release_id=started["release_id"],
        action="canary",
        target_revision=context["target_revision"],
        params=context["params"],
    )
    release_a.grant_approval(action_grant)

    svc_b, _release_b, wo_b, report_b, cs_b = _prepared(sqlite_session, 16)
    svc_b.attach_gate(
        cs_b,
        eval_id=report_b["eval_id"],
        report_hash=wo_b["gate_report_ref"]["digest"].removeprefix("sha256:"),
    )
    svc_b.request_approval(
        cs_b,
        workorder_hash=wo_b["hash"],
        nonce=wo_b["nonce"],
        expiry=wo_b["expiry"],
    )

    with pytest.raises(ChangeSetServiceError) as exc:
        svc_b.approve(
            cs_b,
            approval_id=action_grant["approval_id"],
            approver="human-1",
            workorder_hash=wo_b["hash"],
        )

    assert exc.value.code == "hash_mismatch"
