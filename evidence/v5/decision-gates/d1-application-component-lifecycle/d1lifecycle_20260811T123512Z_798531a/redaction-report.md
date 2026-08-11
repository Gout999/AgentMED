# Redaction report

Scope: added lines across cumulative D1 semantic range
`688e5d91182d73a7c0f0652a16377676602bedd7..798531af539cd37e797723f2985d55c70fa1046e`.

- Added lines scanned: 864
- High-confidence secret findings: 0
- PII candidates before evidence packaging: 2 source-runtime path occurrences
- Credential-bearing URLs: 0
- Private-key or forbidden credential files: 0
- Redactions applied: both source-runtime paths normalized to `$CASELOOP_SOURCE`
- Unresolved secret/PII findings: 0

The evidence records only non-secret tool/runtime identities. No environment dump, resolved Compose
configuration, credential value, provider token, or personal filesystem path is included.
