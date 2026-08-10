# Independent verifier report

Final result: **PASS**. Remaining P0: **0**. Remaining P1: **0** for the Stage 0 scope.

The first independent pass reproduced all 394 then-current tests and found two untested authority-equivocation paths: duplicate `event_id`/`audit_ref` values could be silently overwritten, and the same immutable `(kind,id,revision)` could carry two different digests with separately consistent authority records.

The implementation was changed to reject duplicate event/audit keys, duplicate subject identities and duplicate AuthorityReceipt subject identities. Three focused regression groups were added. The verifier then replayed the original coordinated attacks and confirmed both stable violation codes:

- `authority.subject_identity_duplicate`
- `authority.receipt_subject_identity_duplicate`

The final suite passed 397 tests. The verifier also confirmed exact ModelCall provenance, Resolution anti-self-review, Generator/Judge separation, GateTrack four-upstream binding, typed Gate receipt set equality, Controller actor checks, self-hashes, the Proposal causality order, absence of temporary generator scripts and absence of live/provider claims.

The remaining Judge model-policy and threshold digests are explicitly classified as contract-only pointers. Stage 3 typed execution binding is a hard entry requirement before they may support a real Judge or live Gate.
