"""Link customer orders to user accounts.

Revision ID: a7d25e640f31
Revises: 9a41c78c2d10
Create Date: 2026-11-12 13:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a7d25e640f31"
down_revision = "9a41c78c2d10"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("order", sa.Column("user_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("order") as batch_op:
        batch_op.create_foreign_key(
            "fk_order_user_id_user",
            "user",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_order_user_id", ["user_id"], unique=False)


def downgrade():
    with op.batch_alter_table("order") as batch_op:
        batch_op.drop_index("ix_order_user_id")
        batch_op.drop_constraint("fk_order_user_id_user", type_="foreignkey")
        batch_op.drop_column("user_id")
