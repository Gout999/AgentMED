# ADR review

Result: **PASS**.

- D-008 freezes one owner per command/resource and prevents projections, exporters and AutomationRun views from becoming a second authority.
- D-009 separates governed Agent, WorkerTask, Attempt, platform worker, native runtime session, model call and Controller causation.
- D-010 freezes capability, secret and public-principal boundaries without distributing internal controller tokens.
- D-011 freezes the acyclic chain `sandbox output sealed -> ChangeProposal -> ProposalDecision -> controlled action -> terminal CandidateRevision -> Gate -> WorkOrder`.
- D-012 keeps v3 and v4 contracts parallel and forbids dual lease/dual authority during cutover.

Stage 4 authorization and external execution remain explicitly deferred and fail closed in Stage 0.
