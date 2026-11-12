import uuid
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import CheckConstraint

from app import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'admin', 'owner')", name="ck_user_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False, default="customer")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="scrypt")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    orders = db.relationship("Order", back_populates="user")


class AliExpressToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    refresh_expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    account_user_id = db.Column(db.String(128))
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False)
    slug = db.Column(db.String(200), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    compare_at_price = db.Column(db.Numeric(10, 2))
    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    sku = db.Column(db.String(80), unique=True, nullable=False)
    is_affiliate = db.Column(db.Boolean, nullable=False, default=False, index=True)
    affiliate_url = db.Column(db.String(2048))
    aliexpress_item_id = db.Column(db.String(80))
    aliexpress_product_id = db.Column(db.String(80), index=True)
    aliexpress_sku_attr = db.Column(db.String(255))
    aliexpress_source_url = db.Column(db.String(2048))
    aliexpress_stock_status = db.Column(db.String(80))
    supplier_price = db.Column(db.Numeric(10, 2))
    commission_rate = db.Column(db.Float, nullable=False, default=0.0)
    category = db.Column(db.String(80), nullable=False)
    images = db.Column(db.JSON, nullable=False, default=list)
    is_featured = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    order_items = db.relationship("OrderItem", back_populates="product")


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), index=True)
    aliexpress_order_id = db.Column(db.String(255), index=True)
    public_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()), index=True)
    customer_name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(254), nullable=False, index=True)
    phone = db.Column(db.String(40), nullable=False)
    address_line1 = db.Column(db.String(200), nullable=False)
    address_line2 = db.Column(db.String(200))
    city = db.Column(db.String(100), nullable=False)
    region = db.Column(db.String(100), nullable=False)
    postal_code = db.Column(db.String(30), nullable=False)
    country = db.Column(db.String(2), nullable=False)
    payment_status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    payment_provider = db.Column(db.String(40), nullable=False, default="unconfigured")
    payment_reference = db.Column(db.String(200))
    fulfillment_status = db.Column(db.String(20), nullable=False, default="unfulfilled")
    fulfillment_error = db.Column(db.Text)
    tracking_number = db.Column(db.String(100))
    logistics_service = db.Column(db.String(120))
    total_amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", back_populates="orders")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id", ondelete="CASCADE"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    product_title = db.Column(db.String(180), nullable=False)
    sku = db.Column(db.String(80), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)
    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product", back_populates="order_items")


class AffiliateClick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="SET NULL"), index=True)
    clicked_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    user_agent = db.Column(db.String(512))
    referrer = db.Column(db.String(2048))
    destination_url = db.Column(db.String(2048), nullable=False)
    product = db.relationship("Product")
