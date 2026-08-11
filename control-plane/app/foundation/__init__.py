"""CaseLoop V5 C2 foundation package.

Closed record primitives, major-aware event specifications, exact-binding
validation and the canonical graph verifier. The foundation defines data and
verification mechanics only: it never owns domain commands, capability
activation, transport generation or coordinator decisions, and it never
imports a domain service, API, CLI, Console or adapter (enforced by
``tests/test_v5_c2_foundation.py``).

Import rule: stdlib + ``app.models`` + ``app.utils`` only.
"""
