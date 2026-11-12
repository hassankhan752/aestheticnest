"""Add AliExpress OAuth, catalog mappings, and fulfillment fields.

Revision ID: b4816de2a903
Revises: a7d25e640f31
Create Date: 2026-11-12 14:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b4816de2a903"
down_revision = "a7d25e640f31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "aliexpress_token",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_user_id", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("product", sa.Column("aliexpress_product_id", sa.String(length=80), nullable=True))
    op.add_column("product", sa.Column("aliexpress_sku_attr", sa.String(length=255), nullable=True))
    op.add_column("product", sa.Column("aliexpress_source_url", sa.String(length=2048), nullable=True))
    op.add_column("product", sa.Column("aliexpress_stock_status", sa.String(length=80), nullable=True))
    op.add_column("product", sa.Column("supplier_price", sa.Numeric(precision=10, scale=2), nullable=True))
    op.create_index("ix_product_aliexpress_product_id", "product", ["aliexpress_product_id"], unique=False)

    op.add_column("order", sa.Column("aliexpress_order_id", sa.String(length=255), nullable=True))
    op.add_column("order", sa.Column("logistics_service", sa.String(length=120), nullable=True))
    op.add_column("order", sa.Column("fulfillment_error", sa.Text(), nullable=True))
    op.create_index("ix_order_aliexpress_order_id", "order", ["aliexpress_order_id"], unique=False)


def downgrade():
    op.drop_index("ix_order_aliexpress_order_id", table_name="order")
    op.drop_column("order", "fulfillment_error")
    op.drop_column("order", "logistics_service")
    op.drop_column("order", "aliexpress_order_id")

    op.drop_index("ix_product_aliexpress_product_id", table_name="product")
    op.drop_column("product", "supplier_price")
    op.drop_column("product", "aliexpress_stock_status")
    op.drop_column("product", "aliexpress_source_url")
    op.drop_column("product", "aliexpress_sku_attr")
    op.drop_column("product", "aliexpress_product_id")

    op.drop_table("aliexpress_token")
