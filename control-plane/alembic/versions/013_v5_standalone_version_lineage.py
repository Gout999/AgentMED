"""Add standalone record lineage to system_version_sets.

Revision ID: 013
Revises: 012
Create Date: 2026-08-12

R3-full activates the standalone ``system-versions.record`` intent.  A
standalone record creates the second and later immutable SystemVersionSet
from the existing authority graph; its exact previous VersionSet binding
(canonical CAS lineage) must be persisted on the record itself.

This migration is purely additive: it adds one nullable JSON column,
``exact_previous_system_version_set_binding_or_null``, to
``system_version_sets``.  Bootstrap-created first version sets and every
existing row keep a NULL previous binding; R3 standalone records populate it.
No existing row, constraint, event, receipt or audit is rewritten, so the
upgrade is safe on databases that already carry V5 history (unlike the
fail-closed 011/012 envelope migrations).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_version_sets",
        sa.Column(
            "exact_previous_system_version_set_binding_or_null",
            sa.JSON(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "system_version_sets",
        "exact_previous_system_version_set_binding_or_null",
    )
