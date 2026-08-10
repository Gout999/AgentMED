# Stage 1 Entry debt

Stage 1 runtime/API implementation is **NO-GO** until the public wire contract is completed.

Required before implementation:

1. Replace generic OpenAPI `ResourceEnvelope.data`, empty budget and `EmptyCommand` placeholders with exact request/response fields, nullability and error responses for every registered intent.
2. Freeze durable query paths for asynchronous source diagnostics and later external operations.
3. Allow a maintainer report with no trace to create truthful `UNKNOWN` evidence without inventing an AgentRunRef.
4. Align public idempotency resource kinds with the owner matrix.
5. Freeze workspace-scoped public principals, scopes, expiry, revocation and PostgreSQL idempotency records.
6. Add migrations only after the wire contract passes the Stage 1 Entry verifier.

The first runtime slice is deliberately smaller than Langfuse: an authenticated maintainer submits a no-trace report through HTTP/CLI; one transaction writes Signal, QualityCase link, `UNKNOWN` TraceEvidenceReceipt, event, audit and idempotency response. Langfuse read, CaseLoop OTel write/readback and clean-machine quickstart follow only after that slice passes.
