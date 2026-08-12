"""R3-full standalone system-versions.record unit tests.

Covers the D2-frozen standalone record contract over a real bootstrap graph:
reference-only validation, exact-previous CAS lineage, one-PG-UoW writes,
idempotent replay, and fail-closed rejection of stale/missing/unknown inputs.
"""
from __future__ import annotations

import pytest

from app.public_api.v5_models import SystemVersionRecordRequest
from app.services.system_versions import SystemVersionsError

from test_v5_system_versions import (
    _import,
    _import_principal,
    _manifest,
    _seed_env,
    _service,
    NOW,
    WORKSPACE,
)
from test_v5_application_catalog import _principal_context, _seed_principal

_RECORD_PRINCIPAL = "prn_01J00000000000R3"
_RECORD_SCOPES = [
    "system_manifests:import",
    "system_versions:read",
    "system_versions:record",
]


def _record_principal(**overrides):
    return _principal_context(
        principal_id=_RECORD_PRINCIPAL,
        scopes=_RECORD_SCOPES,
        required_scope="system_versions:record",
        **overrides,
    )


def _record_request(
    *, previous_binding, bindings=None, topology_binding=None, body=None
) -> dict:
    """Build a canonical record request body from import response fragments."""
    return {
        "schema_version": "2.0",
        "application_id": body["application"]["application_id"],
        "environment_id": body["environment"]["environment_id"],
        "exact_component_revision_bindings": bindings or [],
        "exact_topology_revision_binding": topology_binding,
        "exact_previous_system_version_set_binding_or_null": previous_binding,
    }


def _bootstrap(session) -> dict:
    """Run the real one-shot bootstrap import and return its response body."""
    _seed_env(session)
    _seed_principal(
        session,
        principal_id=_RECORD_PRINCIPAL,
        scopes=_RECORD_SCOPES,
        trust_roles=["integrator"],
    )
    session.commit()
    response = _import(session, _manifest())
    session.commit()
    return response.model_dump(mode="json")


def _second_record_request(body: dict, *, subset: bool = True) -> dict:
    version_set = body["system_version_set"]
    revisions = version_set["exact_component_revision_bindings"]
    if subset:
        revisions = revisions[:1]
    return _record_request(
        previous_binding={
            "kind": "SYSTEM_VERSION_SET",
            "id": version_set["system_version_set_id"],
            "revision": 1,
            "digest": version_set["record_envelope"]["record_digest"],
        },
        bindings=revisions,
        topology_binding=version_set["exact_topology_revision_binding"],
        body=body,
    )


def test_record_creates_second_immutable_version_set(sqlite_session) -> None:
    body = _bootstrap(sqlite_session)
    service = _service(sqlite_session)
    request = SystemVersionRecordRequest.model_validate(
        _second_record_request(body)
    )
    response = service.record_system_version(
        request,
        principal=_record_principal(),
        idempotency_key="record-key-0001",
        request_id="req_01J000000000000R",
    )
    sqlite_session.commit()

    version_set = response.system_version_set
    # Every version set is a distinct immutable subject created at revision 1;
    # the lineage is carried by the exact previous binding.
    assert version_set.record_envelope.revision == 1
    assert version_set.system_version_set_id != body["system_version_set"][
        "system_version_set_id"
    ]
    assert version_set.exact_previous_system_version_set_binding_or_null.model_dump(
        mode="json"
    ) == {
        "kind": "SYSTEM_VERSION_SET",
        "id": body["system_version_set"]["system_version_set_id"],
        "revision": 1,
        "digest": body["system_version_set"]["record_envelope"]["record_digest"],
    }
    assert response.idempotency.replayed is False
    # the second version set is readable through the R3 get path
    from app.services.system_versions import SystemVersionsService
    from test_v5_system_versions import _reader_principal

    got = SystemVersionsService(sqlite_session, clock=lambda: NOW).get_system_version(
        version_set.system_version_set_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000G",
    )
    assert got.system_version_set.system_version_set_id == (
        version_set.system_version_set_id
    )
    assert got.system_version_set.exact_previous_system_version_set_binding_or_null == (
        version_set.exact_previous_system_version_set_binding_or_null
    )


def test_record_cas_rejects_stale_previous_binding(sqlite_session) -> None:
    body = _bootstrap(sqlite_session)
    service = _service(sqlite_session)
    request_body = _second_record_request(body)
    request_body["exact_previous_system_version_set_binding_or_null"]["digest"] = (
        "sha256:" + "0" * 64
    )
    request = SystemVersionRecordRequest.model_validate(request_body)
    with pytest.raises(SystemVersionsError) as excinfo:
        service.record_system_version(
            request,
            principal=_record_principal(),
            idempotency_key="record-key-0002",
            request_id="req_01J000000000000R",
        )
    assert excinfo.value.code == "CATALOG_CONFLICT"
    assert excinfo.value.details == {"reason": "STALE_EXACT_PREVIOUS_BINDING"}


def test_record_requires_exact_previous_binding_from_second_version(
    sqlite_session,
) -> None:
    body = _bootstrap(sqlite_session)
    service = _service(sqlite_session)
    request_body = _second_record_request(body)
    request_body["exact_previous_system_version_set_binding_or_null"] = None
    request = SystemVersionRecordRequest.model_validate(request_body)
    with pytest.raises(SystemVersionsError) as excinfo:
        service.record_system_version(
            request,
            principal=_record_principal(),
            idempotency_key="record-key-0003",
            request_id="req_01J000000000000R",
        )
    assert excinfo.value.code == "REQUEST_INVALID"
    assert excinfo.value.details == {
        "reason": "EXACT_PREVIOUS_BINDING_REQUIRED_FROM_SECOND_VERSION"
    }


def test_record_rejects_unknown_component_revision_binding(sqlite_session) -> None:
    body = _bootstrap(sqlite_session)
    service = _service(sqlite_session)
    request_body = _second_record_request(body)
    request_body["exact_component_revision_bindings"].append(
        {
            "kind": "COMPONENT_REVISION",
            "id": "crv_01J0000000000ZZZZ",
            "revision": 1,
            "digest": "sha256:" + "0" * 64,
        }
    )
    request = SystemVersionRecordRequest.model_validate(request_body)
    with pytest.raises(SystemVersionsError) as excinfo:
        service.record_system_version(
            request,
            principal=_record_principal(),
            idempotency_key="record-key-0004",
            request_id="req_01J000000000000R",
        )
    assert excinfo.value.code == "REQUEST_INVALID"
    assert excinfo.value.details == {"reason": "UNKNOWN_COMPONENT_REVISION_BINDING"}


def test_record_same_key_same_body_replays_with_zero_new_facts(sqlite_session) -> None:
    body = _bootstrap(sqlite_session)
    service = _service(sqlite_session)
    request = SystemVersionRecordRequest.model_validate(_second_record_request(body))
    first = service.record_system_version(
        request,
        principal=_record_principal(),
        idempotency_key="record-key-0005",
        request_id="req_01J000000000000R",
    )
    sqlite_session.commit()
    second = service.record_system_version(
        request,
        principal=_record_principal(),
        idempotency_key="record-key-0005",
        request_id="req_01J000000000000R",
    )
    sqlite_session.commit()

    assert second.idempotency.replayed is True
    assert second.system_version_set.system_version_set_id == (
        first.system_version_set.system_version_set_id
    )
    from test_v5_system_versions import _count
    from app.models.v5_tables import SystemVersionSet

    assert _count(sqlite_session, SystemVersionSet) == 2


def test_record_same_key_different_body_conflicts(sqlite_session) -> None:
    body = _bootstrap(sqlite_session)
    service = _service(sqlite_session)
    first = _second_record_request(body)
    request = SystemVersionRecordRequest.model_validate(first)
    service.record_system_version(
        request,
        principal=_record_principal(),
        idempotency_key="record-key-0006",
        request_id="req_01J000000000000R",
    )
    sqlite_session.commit()
    drift = _second_record_request(body, subset=False)
    drifted = SystemVersionRecordRequest.model_validate(drift)
    with pytest.raises(SystemVersionsError) as excinfo:
        service.record_system_version(
            drifted,
            principal=_record_principal(),
            idempotency_key="record-key-0006",
            request_id="req_01J000000000000R",
        )
    assert excinfo.value.code == "IDEMPOTENCY_CONFLICT"
