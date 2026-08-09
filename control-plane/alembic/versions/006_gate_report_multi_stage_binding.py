"""allow every gate stage to bind the same immutable WorkOrder

Revision ID: 006
Revises: 005
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SQLITE_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _workorder_unique_name() -> str | None:
    bind = op.get_bind()
    for constraint in sa.inspect(bind).get_unique_constraints("gate_reports"):
        if constraint.get("column_names") == ["workorder_hash"]:
            name = constraint.get("name")
            return str(name) if name else None
    return None


def upgrade() -> None:
    bind = op.get_bind()
    name = _workorder_unique_name()
    if bind.dialect.name == "sqlite":
        # SQLite created the 002 inline UNIQUE constraint without a name.
        # Batch reflection plus a deterministic naming convention makes it
        # removable while preserving all existing GateReport rows.
        with op.batch_alter_table(
            "gate_reports", naming_convention=_SQLITE_NAMING
        ) as batch:
            batch.drop_constraint(
                name or "uq_gate_reports_workorder_hash", type_="unique"
            )
    elif name:
        op.drop_constraint(name, "gate_reports", type_="unique")
    else:
        raise RuntimeError("gate_reports.workorder_hash UNIQUE constraint is missing")
    op.create_index(
        "ix_gate_reports_workorder_hash",
        "gate_reports",
        ["workorder_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_gate_reports_workorder_hash", table_name="gate_reports")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("gate_reports") as batch:
            batch.create_unique_constraint(
                "uq_gate_reports_workorder_hash", ["workorder_hash"]
            )
    else:
        op.create_unique_constraint(
            "uq_gate_reports_workorder_hash",
            "gate_reports",
            ["workorder_hash"],
        )
