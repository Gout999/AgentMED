# P0-4 B1 Verification

- Friend baseline: `origin/main` at
  `a6de5cc1a06d6967634676b2661da7d2e46d287b`.
- Replay implementation commit:
  `b5ef38fbc1a0da90bf766f0590763be5d1118e74`.
- Review-remediation evidence commit:
  `d0fb6ccb336a13982bc882e5b80c8705b57b62d7`.
- Formal replay manifest:
  `b1run_2f4b11832c2e4dbb/b1-run-manifest.json`.
- Independent validation: passed without `--allow-dirty`; 22 required artifacts
  and 135 probe outputs were digest-verified.
- All 22 manifest artifact references use repository-relative `repo:///` URIs;
  no `file://` reference is committed. The run can therefore be validated from
  any checkout at the recorded commit.
- The replay exports one persisted WorkOrder and both persisted GateReports.
  Their WorkOrder hash, target revision, authorization digest, evidence digest,
  and `probes-customer-service-v1` dataset binding are independently checked.
- Test reports embedded in the bundle: 29 contract assertions passed and 28
  replay assertions passed.
- Authoritative outcome: duplicate complaint collapsed to one Case; attribution
  is `ATTRIBUTED` with `fault_layer=prompt`; the exact WorkOrder/Gate/Approval
  chain promoted; notification used the explicitly labelled `feishu-mock`
  replay adapter; the Case closed; Trust added one action sample and denied
  promotion.
- Independent verifier result: PASS for fake-success removal and fail-closed
  provider-origin enforcement. Live Gate persistence requires the official
  StepFun origin in both the evaluator response and the independent Quality log.

Third-party verification from the repository root:

```bash
python scripts/validate_b1_run.py \
  evidence/p0/p0-4-b1/b1run_2f4b11832c2e4dbb/b1-run-manifest.json
```

To generate a fresh machine-local run instead, use `make demo-b1-replay`.

## Live-provider boundary

The separately executed live preflight report is:

`../p0-4-b1-live/b1run_livef66526ac52dd4e0f/live-provider-report.json`

It exited with status 2 before any provider call because required credentials,
deployed endpoints, fresh human ApprovalGrants, independent AgentTeams trace
attestation, and a post-injection Feishu message command were unavailable. It
did not fall back to replay. Therefore P0-4 remains `BLOCKED`, not `DONE`.

The next repository-local requirement is durable resume or terminal failure
reconciliation after interruption following Case creation and after promotion.
