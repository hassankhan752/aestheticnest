"""Add customer roles and user update timestamps.

Revision ID: 9a41c78c2d10
Revises: c376e2bc8717
Create Date: 2026-11-12 12:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9a41c78c2d10"
down_revision = "c376e2bc8717"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(sa.text('UPDATE "user" SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL'))
    op.execute(
        sa.text(
            'UPDATE "user" SET role = \'customer\' '
            "WHERE role NOT IN ('customer', 'admin', 'owner')"
        )
    )
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_user_role",
            "role IN ('customer', 'admin', 'owner')",
        )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("ck_user_role", type_="check")
        batch_op.drop_column("updated_at")
