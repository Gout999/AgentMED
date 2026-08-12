"""V5-2B durable public-operation projection and authority tests."""
from __future__ import annotations

import copy
import sqlalchemy as sa
import pytest

from app.models import Audit, Event, Outbox
from app.models.v4_tables import AuthorityReceipt, PublicCommandIdempotency, PublicPrincipal
from app.models.v5_work_tables import AutomationRequest, WorkAttempt, WorkTask
from app.public_api.v5_models import InvestigationStartRequest, OperationCancelRequest
from app.services.public_operations import (
    PublicOperationError,
    PublicOperationReadDenial,
    PublicOperationService,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.v5_work_kernel import _attempt_snapshot
from app.utils.v4_integrity import canonical_digest
from v5_public_operation_fixtures import (
    CASE,
    NOW,
    OTHER_PRINCIPAL,
    PRINCIPAL,
    WORKSPACE,
    principal_context,
    seed_public_operation_world,
)


def _service(session) -> PublicOperationService:
    return PublicOperationService(
        session, cursor_signing_key="v5-2b-test-cursor-key", clock=lambda: NOW
    )


def _start(
    service: PublicOperationService,
    case_digest: str,
    *,
    key: str = "investigation-start-0001",
    instructions: str = "Investigate the exact bound case.",
):
    return service.start_investigation(
        CASE,
        InvestigationStartRequest(
            schema_version="2.0",
            case_revision=1,
            case_digest=case_digest,
            instructions=instructions,
            max_attempts=2,
        ),
        principal=principal_context("investigations:start"),
        idempotency_key=key,
        request_id="req_01J0000000000P01",
    )


def _count(session, model) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def _complete_task_with_output(
    operations: PublicOperationService,
    *,
    task_id: str,
    output: dict[str, object],
    request_marker: str,
) -> WorkAttempt:
    claimed = operations.work.claim(
        workspace_id=WORKSPACE,
        task_id=task_id,
        worker_identity="fixture-worker",
        request_id=f"req_01J000000000{request_marker}10",
    )
    attempt = operations.work.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=claimed.attempt.attempt_id,
        fencing_token=claimed.attempt.fence_token,
        runtime_adapter="fixture-adapter",
        runtime_session=f"fixture-session-{request_marker}",
        request_id=f"req_01J000000000{request_marker}11",
    )
    receipt_digest = operations.work.record_terminal_receipt(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        issuer="fixture-worker",
        process_exit_code=0,
        stream_complete=True,
        structured_output_valid=True,
        request_id=f"req_01J000000000{request_marker}12",
    )
    operations.work.record_output(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        output_payload=output,
        stream_complete=True,
        request_id=f"req_01J000000000{request_marker}13",
    )
    operations.work.complete_attempt(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        terminal_receipt_digest=receipt_digest,
        request_id=f"req_01J000000000{request_marker}14",
    )
    return attempt


def test_start_is_one_durable_fact_chain_and_same_key_replays(sqlite_session) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    service = _service(sqlite_session)

    first = _start(service, case_digest)
    sqlite_session.commit()
    replay = _start(service, case_digest)

    assert first.operation.state == "SUBMITTED"
    assert replay.operation.operation_id == first.operation.operation_id
    assert replay.operation.exact_work_task_binding.id == first.operation.exact_work_task_binding.id
    assert replay.idempotency.replayed is True
    assert _count(sqlite_session, AutomationRequest) == 1
    assert _count(sqlite_session, WorkTask) == 1
    assert _count(sqlite_session, PublicCommandIdempotency) == 1
    event_types = set(sqlite_session.scalars(sa.select(Event.event_type)).all())
    assert {"work.requested", "automation_request.investigation_submitted"} <= event_types
    assert _count(sqlite_session, AuthorityReceipt) >= 2
    assert _count(sqlite_session, Outbox) >= 2
    assert _count(sqlite_session, Audit) >= 4  # registrations + both command audits

    with pytest.raises(PublicOperationError) as exc:
        _start(
            service,
            case_digest,
            key="investigation-start-0001",
            instructions="Different body under the same key.",
        )
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_cancel_is_a_durable_stop_request_not_fake_terminal_state(sqlite_session) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    service = _service(sqlite_session)
    started = _start(service, case_digest)
    sqlite_session.commit()

    response = service.request_cancel(
        started.operation.operation_id,
        OperationCancelRequest(schema_version="2.0", reason="operator requested stop"),
        principal=principal_context("operations:cancel"),
        idempotency_key="cancel-request-0001",
        request_id="req_01J0000000000P02",
    )
    sqlite_session.commit()
    replay = service.request_cancel(
        started.operation.operation_id,
        OperationCancelRequest(schema_version="2.0", reason="operator requested stop"),
        principal=principal_context("operations:cancel"),
        idempotency_key="cancel-request-0001",
        request_id="req_01J0000000000P03",
    )

    task = sqlite_session.get(WorkTask, started.operation.exact_work_task_binding.id)
    row = sqlite_session.scalar(sa.select(AutomationRequest))
    assert task is not None and task.state == "QUEUED"
    assert row is not None and row.stop_requested is True
    assert response.operation.state == "CANCEL_REQUESTED"
    assert response.operation.cancel_requested is True
    assert replay.operation.state == "CANCEL_REQUESTED"
    assert replay.idempotency.replayed is True
    assert _count(sqlite_session, AutomationRequest) == 1
    assert (
        _count(sqlite_session, PublicCommandIdempotency) == 2
    )  # start + cancel


def test_get_and_list_filter_visibility_before_signed_pagination(sqlite_session) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    service = _service(sqlite_session)
    created = [
        _start(service, case_digest, key=f"investigation-list-000{index}")
        for index in range(1, 4)
    ]
    sqlite_session.commit()

    first_page = service.list_operations(
        principal=principal_context("operations:read"),
        request_id="req_01J0000000000P04",
        limit=1,
        cursor=None,
    )
    assert len(first_page.items) == 1
    assert first_page.next_cursor is not None
    second_page = service.list_operations(
        principal=principal_context("operations:read"),
        request_id="req_01J0000000000P05",
        limit=1,
        cursor=first_page.next_cursor,
    )
    assert len(second_page.items) == 1
    assert second_page.items[0].operation_id != first_page.items[0].operation_id

    tampered_cursor = first_page.next_cursor[:-1] + (
        "A" if first_page.next_cursor[-1] != "A" else "B"
    )
    with pytest.raises(PublicOperationReadDenial) as tampered:
        service.list_operations(
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000P06",
            limit=1,
            cursor=tampered_cursor,
        )
    assert tampered.value.code == "REQUEST_INVALID"
    with pytest.raises(PublicOperationReadDenial) as stale_scope:
        service.list_operations(
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000P07",
            limit=2,
            cursor=first_page.next_cursor,
        )
    assert stale_scope.value.code == "REQUEST_INVALID"

    opaque = principal_context(
        "operations:read",
        principal_id=OTHER_PRINCIPAL,
        project_ids=[],
        environment_ids=[],
    )
    hidden = service.list_operations(
        principal=opaque,
        request_id="req_01J0000000000P08",
        limit=100,
        cursor=None,
    )
    assert hidden.items == []
    with pytest.raises(PublicOperationReadDenial) as denied:
        service.get_operation(
            created[0].operation.operation_id,
            principal=opaque,
            request_id="req_01J0000000000P09",
        )
    assert denied.value.code == "RESOURCE_NOT_FOUND"
    assert denied.value.audit_ref.startswith("audit://")

    cross_workspace = "ws_01J0000000000P99"
    cross_principal_id = "prn_01J0000000000P99"
    cross = principal_context(
        "operations:read",
        principal_id=cross_principal_id,
    )
    cross = cross.model_copy(
        update={
            "workspace_id": cross_workspace,
            "requested_context": cross.requested_context.model_copy(
                update={"workspace_id": cross_workspace}
            ),
        }
    )
    sqlite_session.add(
        PublicPrincipal(
            principal_id=cross_principal_id,
            workspace_id=cross_workspace,
            principal_type=cross.principal_type,
            state="ACTIVE",
            subject_digest="sha256:" + "9" * 64,
            audiences=list(cross.audiences),
            project_ids=list(cross.project_ids),
            environment_ids=list(cross.environment_ids),
            trust_roles=[],
            scopes=list(cross.scopes),
            claims_digest=cross.claims_digest,
            revoked_at=None,
        )
    )
    sqlite_session.flush()
    assert service.list_operations(
        principal=cross,
        request_id="req_01J0000000000P97",
        limit=100,
        cursor=None,
    ).items == []
    with pytest.raises(PublicOperationReadDenial) as cross_tenant:
        service.get_operation(
            created[0].operation.operation_id,
            principal=cross,
            request_id="req_01J0000000000P98",
        )
    assert cross_tenant.value.code == "RESOURCE_NOT_FOUND"


def test_completed_requires_trusted_domain_artifact_but_not_pass_verdict(sqlite_session) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    operations = _service(sqlite_session)
    started = _start(operations, case_digest)
    task_id = started.operation.exact_work_task_binding.id

    payload = {"summary": "Bug confirmed; gate has not run.", "gate_status": "NOT_RUN"}
    output = {
        "artifact_kind": "INVESTIGATION_REPORT",
        "schema_major": 2,
        "domain_verdict": "FAILED",
        "evidence_completeness": "COMPLETE",
        "exact_artifact_binding": {
            "kind": "INVESTIGATION_REPORT",
            "id": "report_01J0000000000P01",
            "revision": 1,
            "digest": canonical_digest(payload),
        },
        "payload": payload,
    }
    attempt = _complete_task_with_output(
        operations,
        task_id=task_id,
        output=output,
        request_marker="P",
    )

    completed = operations.get_operation(
        started.operation.operation_id,
        principal=principal_context("operations:read"),
        request_id="req_01J0000000000P15",
    )
    assert completed.operation.state == "COMPLETED"
    assert completed.operation.artifact_or_null is not None
    assert completed.operation.artifact_or_null.domain_verdict == "FAILED"
    assert completed.operation.artifact_or_null.payload["gate_status"] == "NOT_RUN"

    stored_attempt = sqlite_session.get(WorkAttempt, attempt.attempt_id)
    assert stored_attempt is not None
    malformed = copy.deepcopy(stored_attempt.output_payload)
    malformed["exact_artifact_binding"]["digest"] = "sha256:" + "0" * 64
    stored_attempt.output_payload = malformed
    stored_attempt.output_digest = canonical_digest(malformed)
    stored_attempt.record_digest = canonical_digest(_attempt_snapshot(stored_attempt))
    with pytest.raises(PublicOperationError) as tampered:
        operations.get_operation(
            started.operation.operation_id,
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000P16",
        )
    assert tampered.value.code == "INTERNAL_ERROR"


def test_authoritative_but_invalid_artifact_projects_failed(sqlite_session) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    operations = _service(sqlite_session)
    started = _start(
        operations,
        case_digest,
        key="investigation-invalid-artifact-0001",
    )
    payload = {"summary": "Malformed binding", "gate_status": "NOT_RUN"}
    invalid_output = {
        "artifact_kind": "INVESTIGATION_REPORT",
        "schema_major": 2,
        "domain_verdict": "UNKNOWN",
        "evidence_completeness": "UNKNOWN",
        "exact_artifact_binding": {
            "kind": "INVESTIGATION_REPORT",
            "id": "report_01J0000000000P02",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
        },
        "payload": payload,
    }
    _complete_task_with_output(
        operations,
        task_id=started.operation.exact_work_task_binding.id,
        output=invalid_output,
        request_marker="Q",
    )
    untrusted = operations.get_operation(
        started.operation.operation_id,
        principal=principal_context("operations:read"),
        request_id="req_01J0000000000Q15",
    )
    assert untrusted.operation.state == "FAILED"
    assert untrusted.operation.artifact_or_null is None


def test_unknown_and_revocation_fail_closed(sqlite_session) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    service = _service(sqlite_session)
    started = _start(service, case_digest)
    task_id = started.operation.exact_work_task_binding.id
    claimed = service.work.claim(
        workspace_id=WORKSPACE,
        task_id=task_id,
        worker_identity="unknown-worker",
        request_id="req_01J0000000000P17",
    )
    attempt = service.work.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=claimed.attempt.attempt_id,
        fencing_token=claimed.attempt.fence_token,
        runtime_adapter="fixture-adapter",
        runtime_session="unknown-session",
        request_id="req_01J0000000000P18",
    )
    service.work.mark_attempt_unknown(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=attempt.attempt_id,
        ambiguity_reason="worker disappeared after external timeout",
        request_id="req_01J0000000000P19",
    )
    projected = service.get_operation(
        started.operation.operation_id,
        principal=principal_context("operations:read"),
        request_id="req_01J0000000000P20",
    )
    assert projected.operation.state == "INPUT_REQUIRED"
    assert projected.operation.artifact_or_null is None

    principal_row = sqlite_session.get(PublicPrincipal, PRINCIPAL)
    assert principal_row is not None
    principal_row.state = "REVOKED"
    principal_row.revoked_at = NOW
    with pytest.raises(PublicOperationError) as revoked:
        service.get_operation(
            started.operation.operation_id,
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000P21",
        )
    assert revoked.value.code == "SCOPE_FORBIDDEN"


def test_late_command_audit_failure_rolls_back_every_operation_fact(
    sqlite_session,
) -> None:
    case_digest = seed_public_operation_world(sqlite_session)
    before = {
        model: _count(sqlite_session, model)
        for model in (
            AutomationRequest,
            WorkTask,
            Event,
            Outbox,
            AuthorityReceipt,
            PublicCommandIdempotency,
        )
    }
    delegate = V4AuditService(sqlite_session, clock=lambda: NOW)

    class FailLateAudit:
        def record(self, **kwargs):
            if kwargs.get("action") == "investigations.start":
                raise V4AuditUnavailable("forced late audit failure")
            return delegate.record(**kwargs)

    service = PublicOperationService(
        sqlite_session,
        cursor_signing_key="v5-2b-test-cursor-key",
        clock=lambda: NOW,
        audit=FailLateAudit(),  # type: ignore[arg-type]
    )
    with pytest.raises(PublicOperationError) as failed:
        _start(service, case_digest, key="investigation-audit-fail-0001")
    assert failed.value.code == "AUDIT_UNAVAILABLE"
    sqlite_session.rollback()
    assert {
        model: _count(sqlite_session, model)
        for model in before
    } == before
