# V5 First System Case local runbook

> **R4 WORKTREE RUNBOOK / NOT YET A CLEAN-CHECKOUT CAPABILITY.** The commands below
> describe the reviewed V5-1A/B/C repair worktree. Do not treat this file's presence
> as stage closure. It becomes the supported clean-checkout path only after R1-R4
> semantic commits, evidence manifests and post-commit verifiers pass.

This is the intended local V5-1A/B/C management path. Credential creation and
rotation are **not** public HTTP operations: all three management calls below
run `python -m app.bootstrap.v5_catalog_local` on the control-plane host and
write PostgreSQL in one local transaction. Public API/CLI calls begin only
after the corresponding bearer has been provisioned.

## Preconditions

Migration 012 rejects legacy V5 authority/event history rather than relabeling it. Use this runbook
only on a disposable/rebuilt local database or after an explicit export-verify-replay recovery has
been completed for data that must be retained.

From `control-plane/`:

```bash
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8090
```

In a second shell, set the non-secret identifiers. Every principal,
credential, source, and controller ID must be unique and match its AgentMED ID
pattern.

```bash
set -euo pipefail
export CL_WORKSPACE_ID=ws_01J0000000000L01
export CL_PROJECT_ID=proj_01J0000000000L01
export CL_OWNER_PRINCIPAL_ID=prn_01J0000000000L01
export CL_OPERATOR_PRINCIPAL_ID=prn_01J0000000000L02
export CL_OWNER_SUBJECT=local-v5-owner
export CL_OPERATOR_SUBJECT=local-v5-operator
export CL_SOURCE_ID=src_01J0000000000L01
export CL_INITIAL_CREDENTIAL_ID=cred_01J0000000000L01
export AGENTMED_API_URL=http://127.0.0.1:8090
export AGENTMED_WORKSPACE_ID="$CL_WORKSPACE_ID"
```

The examples use `jq` and an installed `agentmed` CLI. Set
`CL_MANIFEST_FILE` to a schema-2 JSON manifest that passes:

```bash
export CL_MANIFEST_FILE=/absolute/path/to/system-manifest.json
agentmed --api-version 2 system-manifest validate \
  --manifest-file "$CL_MANIFEST_FILE"
mkdir -p var/v5-first-case-local
```

## 1. Provision project-only operator authority

Generate the initial operator bearer and JTI locally. Store both in the secret
manager named by `secret_storage_ref` before continuing; the bootstrap records
only their digests and does **not** write to a keyring for you.

```bash
export CL_OPERATOR_TOKEN_0=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')
export CL_OPERATOR_JTI_0=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')
```

Then provision the two human principals, the manual source, and all six V4/V5
controller roots. The initial operator and owner grants intentionally contain
no environment because the environment does not exist yet.

```bash
.venv/bin/python - <<'PY' | \
  .venv/bin/python -m app.bootstrap.v5_catalog_local \
  > var/v5-first-case-local/initial-bootstrap.json
import json, os
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
payload = {
    "schema_version": "1.0",
    "workspace_id": os.environ["CL_WORKSPACE_ID"],
    "project_id": os.environ["CL_PROJECT_ID"],
    "owner_principal": {
        "principal_id": os.environ["CL_OWNER_PRINCIPAL_ID"],
        "subject": os.environ["CL_OWNER_SUBJECT"],
    },
    "principal": {
        "principal_id": os.environ["CL_OPERATOR_PRINCIPAL_ID"],
        "subject": os.environ["CL_OPERATOR_SUBJECT"],
    },
    "credential": {
        "credential_id": os.environ["CL_INITIAL_CREDENTIAL_ID"],
        "bearer_token": os.environ["CL_OPERATOR_TOKEN_0"],
        "jti": os.environ["CL_OPERATOR_JTI_0"],
        "issued_at": (now - timedelta(minutes=10)).isoformat(),
        "not_before": (now - timedelta(minutes=5)).isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
    },
    "source": {
        "source_id": os.environ["CL_SOURCE_ID"],
        "connector_kind": "manual",
        "state": "ACTIVE",
        "credential_ref": None,
        "config": {"provider_origin": "https://agentmed.local"},
    },
    "controller": {
        "registration_id": "creg_01J0000000000L01",
        "principal_id": "prn_01J0000000000LC1",
    },
    "version_controller": {
        "registration_id": "creg_01J0000000000L02",
        "principal_id": "prn_01J0000000000LC2",
    },
    "case_controller": {
        "registration_id": "creg_01J0000000000L03",
        "principal_id": "prn_01J0000000000LC3",
    },
    "intake_controllers": {
        "signal": {
            "registration_id": "creg_01J0000000000L04",
            "principal_id": "prn_01J0000000000LC4",
        },
        "case": {
            "registration_id": "creg_01J0000000000L05",
            "principal_id": "prn_01J0000000000LC5",
        },
        "evidence": {
            "registration_id": "creg_01J0000000000L06",
            "principal_id": "prn_01J0000000000LC6",
        },
    },
    "secret_storage_ref": (
        f"keyring://agentmed/local/{os.environ['CL_WORKSPACE_ID']}/operator-initial"
    ),
}
print(json.dumps(payload, separators=(",", ":")))
PY
jq -e '.status == "CREATED" or .status == "REUSED"' \
  var/v5-first-case-local/initial-bootstrap.json
```

Use the initial bearer for the one-shot manifest import:

```bash
export AGENTMED_PUBLIC_TOKEN="$CL_OPERATOR_TOKEN_0"
agentmed --api-version 2 system-manifest import \
  --manifest-file "$CL_MANIFEST_FILE" \
  --idempotency-key v5-first-case-import-0001 \
  > var/v5-first-case-local/manifest-import.json

export CL_APPLICATION_ID=$(jq -er '.application.application_id' \
  var/v5-first-case-local/manifest-import.json)
export CL_ENVIRONMENT_ID=$(jq -er '.environment.environment_id' \
  var/v5-first-case-local/manifest-import.json)
export CL_ENVIRONMENT_REVISION=$(jq -er '.environment.record_envelope.revision' \
  var/v5-first-case-local/manifest-import.json)
export CL_ENVIRONMENT_DIGEST=$(jq -er '.environment.record_envelope.record_digest' \
  var/v5-first-case-local/manifest-import.json)
export CL_SYSTEM_VERSION_SET_ID=$(jq -er '.system_version_set.system_version_set_id' \
  var/v5-first-case-local/manifest-import.json)
```

## 2. Rotate the operator into the imported environment

Do not mutate the initial claims. Generate and save a new bearer/JTI, then bind
that new credential to the exact imported Environment. Its `issued_at` is
captured only after manifest import, so it is strictly later than the
Environment record.

```bash
export CL_ROTATED_CREDENTIAL_ID=cred_01J0000000000L02
export CL_OPERATOR_TOKEN_1=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')
export CL_OPERATOR_JTI_1=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')
export CL_OPERATOR_ROTATED_AT=$(.venv/bin/python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')

.venv/bin/python - <<'PY' | \
  .venv/bin/python -m app.bootstrap.v5_catalog_local \
  > var/v5-first-case-local/operator-rotation.json
import json, os
from datetime import datetime, timedelta

issued = datetime.fromisoformat(os.environ["CL_OPERATOR_ROTATED_AT"])
payload = {
    "schema_version": "1.0",
    "operation": "operator_environment_rotation",
    "workspace_id": os.environ["CL_WORKSPACE_ID"],
    "project_id": os.environ["CL_PROJECT_ID"],
    "principal": {
        "principal_id": os.environ["CL_OPERATOR_PRINCIPAL_ID"],
        "subject": os.environ["CL_OPERATOR_SUBJECT"],
    },
    "previous_credential_id": os.environ["CL_INITIAL_CREDENTIAL_ID"],
    "credential": {
        "credential_id": os.environ["CL_ROTATED_CREDENTIAL_ID"],
        "bearer_token": os.environ["CL_OPERATOR_TOKEN_1"],
        "jti": os.environ["CL_OPERATOR_JTI_1"],
        "issued_at": issued.isoformat(),
        "not_before": issued.isoformat(),
        "expires_at": (issued + timedelta(days=30)).isoformat(),
    },
    "exact_environment_binding": {
        "kind": "ENVIRONMENT",
        "id": os.environ["CL_ENVIRONMENT_ID"],
        "revision": int(os.environ["CL_ENVIRONMENT_REVISION"]),
        "digest": os.environ["CL_ENVIRONMENT_DIGEST"],
    },
    "secret_storage_ref": (
        f"keyring://agentmed/local/{os.environ['CL_WORKSPACE_ID']}/operator-environment"
    ),
}
print(json.dumps(payload, separators=(",", ":")))
PY
jq -e '.status == "CREATED" or .status == "REUSED"' \
  var/v5-first-case-local/operator-rotation.json
export AGENTMED_PUBLIC_TOKEN="$CL_OPERATOR_TOKEN_1"
unset CL_OPERATOR_TOKEN_0 CL_OPERATOR_JTI_0
```

The old credential is now revoked, while its stored claims and empty
environment grant remain unchanged. Use only the rotated bearer for Signal,
binding, and proposal:

```bash
export CL_SIGNAL_AT=$(.venv/bin/python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')
agentmed signal submit \
  --source-id "$CL_SOURCE_ID" \
  --summary 'The bounded tool was not selected' \
  --body 'Fresh local first-system-case report' \
  --reporter-ref "$CL_OPERATOR_SUBJECT" \
  --project-id "$CL_PROJECT_ID" \
  --environment-id "$CL_ENVIRONMENT_ID" \
  --privacy INTERNAL \
  --source-event-id v5-first-case-local-event-0001 \
  --occurred-at "$CL_SIGNAL_AT" \
  --idempotency-key v5-first-case-signal-0001 \
  > var/v5-first-case-local/signal.json

export CL_CASE_ID=$(jq -er '.case.case_id' var/v5-first-case-local/signal.json)
agentmed --api-version 2 case acceptance-criteria get "$CL_CASE_ID" \
  --case-revision 1 > var/v5-first-case-local/readiness-before.json
export CL_CASE_REVISION=$(jq -er '.exact_case_binding.case_revision' \
  var/v5-first-case-local/readiness-before.json)
export CL_CASE_DIGEST=$(jq -er '.exact_case_binding.case_digest' \
  var/v5-first-case-local/readiness-before.json)

agentmed --api-version 2 case bind-application "$CL_CASE_ID" \
  --application-id "$CL_APPLICATION_ID" \
  --environment-id "$CL_ENVIRONMENT_ID" \
  --case-revision "$CL_CASE_REVISION" \
  --case-digest "$CL_CASE_DIGEST" \
  --system-version-set-id "$CL_SYSTEM_VERSION_SET_ID" \
  --idempotency-key v5-first-case-bind-0001 \
  > var/v5-first-case-local/case-binding.json

export CL_ACCEPTANCE_JSON='{"acceptance_source":{"kind":"manual","title":"Wrong tool selected"},"expected_behavior":{"summary":"The bounded tool must be selected."},"applicable_workload_profile":{"name":"local-once","concurrency":"SINGLE"},"applicable_deployment_profile":{"name":"local-shadow","kind":"DEVELOPMENT"}}'
agentmed --api-version 2 case acceptance-criteria propose "$CL_CASE_ID" \
  --case-revision "$CL_CASE_REVISION" \
  --case-digest "$CL_CASE_DIGEST" \
  --acceptance-json "$CL_ACCEPTANCE_JSON" \
  --idempotency-key v5-first-case-propose-0001 \
  > var/v5-first-case-local/proposal.json

export CL_PROPOSAL_ID=$(jq -er '.acceptance_criteria_revision.acceptance_criteria_revision_id' \
  var/v5-first-case-local/proposal.json)
export CL_PROPOSAL_REVISION=$(jq -er '.acceptance_criteria_revision.record_envelope.revision' \
  var/v5-first-case-local/proposal.json)
export CL_PROPOSAL_DIGEST=$(jq -er '.acceptance_criteria_revision.record_envelope.record_digest' \
  var/v5-first-case-local/proposal.json)
```

## 3. Reauthenticate the independent owner and confirm

Only after the proposal exists, complete the owner's local authentication
ceremony, generate a distinct bearer/JTI, and save both in the owner's secret
manager entry. Reusing either operator bearer is rejected.

```bash
export CL_OWNER_CREDENTIAL_ID=cred_01J0000000000L03
export CL_OWNER_TOKEN=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')
export CL_OWNER_JTI=$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')
export CL_OWNER_REAUTH_AT=$(.venv/bin/python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')

.venv/bin/python - <<'PY' | \
  .venv/bin/python -m app.bootstrap.v5_catalog_local \
  > var/v5-first-case-local/owner-reauthentication.json
import json, os
from datetime import datetime, timedelta

issued = datetime.fromisoformat(os.environ["CL_OWNER_REAUTH_AT"])
payload = {
    "schema_version": "1.0",
    "operation": "owner_reauthentication",
    "workspace_id": os.environ["CL_WORKSPACE_ID"],
    "project_id": os.environ["CL_PROJECT_ID"],
    "operator_principal_id": os.environ["CL_OPERATOR_PRINCIPAL_ID"],
    "owner_principal": {
        "principal_id": os.environ["CL_OWNER_PRINCIPAL_ID"],
        "subject": os.environ["CL_OWNER_SUBJECT"],
    },
    "credential": {
        "credential_id": os.environ["CL_OWNER_CREDENTIAL_ID"],
        "bearer_token": os.environ["CL_OWNER_TOKEN"],
        "jti": os.environ["CL_OWNER_JTI"],
        "issued_at": issued.isoformat(),
        "not_before": issued.isoformat(),
        "expires_at": (issued + timedelta(minutes=30)).isoformat(),
    },
    "exact_proposed_revision_binding": {
        "kind": "ACCEPTANCE_CRITERIA_REVISION",
        "id": os.environ["CL_PROPOSAL_ID"],
        "revision": int(os.environ["CL_PROPOSAL_REVISION"]),
        "digest": os.environ["CL_PROPOSAL_DIGEST"],
    },
    "secret_storage_ref": (
        f"keyring://agentmed/local/{os.environ['CL_WORKSPACE_ID']}/owner-reauthentication"
    ),
}
print(json.dumps(payload, separators=(",", ":")))
PY
jq -e '.status == "CREATED" or .status == "REUSED"' \
  var/v5-first-case-local/owner-reauthentication.json

export AGENTMED_PUBLIC_TOKEN="$CL_OWNER_TOKEN"
agentmed --api-version 2 case acceptance-criteria confirm "$CL_PROPOSAL_ID" \
  --case-id "$CL_CASE_ID" \
  --case-revision "$CL_CASE_REVISION" \
  --proposed-revision-digest "$CL_PROPOSAL_DIGEST" \
  --confirmation-note 'Independent owner reauthentication completed.' \
  --idempotency-key v5-first-case-confirm-0001 \
  > var/v5-first-case-local/confirmation.json
jq -e '.acceptance_criteria_revision.confirmation_status == "CONFIRMED"' \
  var/v5-first-case-local/confirmation.json
```

## Secret and human boundary

- The module validates and hashes supplied bearer/JTI material; it does not
  authenticate a person, generate a credential, or save anything to the
  `secret_storage_ref`. A human/local identity layer must do those steps.
- Keep each bearer and JTI long enough to replay the exact local management
  request after an unknown outcome. Never put either value in a manifest,
  receipt file, command argument, Git, or evidence bundle.
- The initial operator credential is project-only. After import it is revoked,
  not rewritten. The rotated operator credential carries the exact environment
  grant. The owner credential is created only after the proposal and must have
  `issued_at` strictly later than `proposed_at`.
- Operator and owner bearers, JTIs, and credential IDs must all be distinct.
  Confirmation is a public API action, but credential issuance and rotation
  remain local management actions with direct database authority.
