# R3-full redaction report

No secret values were recorded. The semantic series contains contract
updates, runtime code, migration 013 and tests; the only secret-shaped tokens
are test identifiers (raw_token/pepper/SecretStr/token_urlsafe) mirroring the
existing R2 integration-test pattern — no credential, token value or key was
added (cumulative added-line scan: 12 identifier-only hits, 0 real findings).
Runtime identities: disposable PostgreSQL only; providers NOT_RUN.
