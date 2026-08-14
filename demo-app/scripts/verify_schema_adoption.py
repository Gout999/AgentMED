#!/usr/bin/env python3
"""Read-only gate before explicitly stamping an existing demo-app database."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schema import SchemaAdoptionError, verify_unversioned_schema_for_adoption


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("REFUSED: DATABASE_URL is required", file=sys.stderr)
        return 2
    try:
        verify_unversioned_schema_for_adoption(database_url)
    except SchemaAdoptionError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    print(
        "VERIFIED: legacy schema matches; no stamp was performed. "
        "Run an explicit Alembic stamp only after operator review."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
