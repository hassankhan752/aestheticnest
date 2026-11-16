import re
import secrets
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func

from app import db, limiter
from app.models import AffiliateClick, AliExpressToken, Order, Product, User
from app.security import admin_required, persistent_limit
from app.services.aliexpress_client import AliExpressAPIError, AliExpressOpenPlatformClient, client_from_config

admin_bp = Blueprint("admin", __name__)


@admin_bp.before_request
def enforce_admin_access():
    if request.endpoint in {"admin.login", "admin.bootstrap"}:
        return None
    if (
        not current_user.is_authenticated
        or not current_user.is_active
        or current_user.role not in {"owner", "admin"}
    ):
        flash("Access denied. Admin privileges required.", "error")
        return redirect(url_for("admin.login"), code=302)
    return None


def _is_aliexpress_tracking_link(url):
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    short_link_hosts = {
        "s.click.aliexpress.com",
        "click.aliexpress.com",
        "a.aliexpress.com",
    }
    tracking_parameters = {
        "aff_fcid", "aff_fsk", "aff_platform", "aff_trace_key", "aff_short_key",
    }
    return (
        hostname in short_link_hosts
        or (hostname.endswith(".aliexpress.com") and parsed.path.startswith("/e/"))
        or bool(tracking_parameters.intersection(parse_qs(parsed.query)))
    )


@admin_bp.route("/login", methods=["GET", "POST"])
@persistent_limit(5, 60, methods=("POST",))
def login():
    if (
        current_user.is_authenticated
        and current_user.is_active
        and current_user.role in {"owner", "admin"}
    ):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email, is_active=True).first() if len(email) <= 254 else None
        if len(password) <= 1024 and user and user.check_password(password) and user.role in {"owner", "admin"}:
            session.clear()
            login_user(user, remember=False, fresh=True)
            return redirect(url_for("admin.dashboard"))
        flash("Admin sign-in failed. Check your credentials and access level.", "error")
    return render_template("admin/login.html", bootstrap_allowed=not User.query.first() and bool(current_app.config["ADMIN_BOOTSTRAP_KEY"]))


@admin_bp.route("/bootstrap", methods=["GET", "POST"])
@limiter.limit("3 per hour", methods=["POST"])
def bootstrap():
    if User.query.first() or not current_app.config.get("ADMIN_BOOTSTRAP_KEY"):
        abort(404)
    if request.method == "POST":
        supplied = request.form.get("bootstrap_key", "")
        expected = current_app.config["ADMIN_BOOTSTRAP_KEY"]
        if not secrets.compare_digest(supplied, expected):
            flash("Bootstrap key was not accepted.", "error")
        else:
            email = (request.form.get("email") or "").strip().lower()[:254]
            password = request.form.get("password", "")
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not 14 <= len(password) <= 1024:
                flash("Use a valid email and a password between 14 and 1024 characters.", "error")
            else:
                user = User(email=email, role="owner")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                session.clear()
                login_user(user, fresh=True)
                flash("Owner account created. Remove the bootstrap key from your .env file now.", "success")
                return redirect(url_for("admin.dashboard"))
    return render_template("admin/bootstrap.html")


@admin_bp.post("/logout")
@admin_required
def logout():
    session.clear()
    logout_user()
    return redirect(url_for("main.home"))


@admin_bp.get("/")
@admin_bp.get("/dashboard")
@admin_required
def dashboard():
    revenue = db.session.query(func.coalesce(func.sum(Order.total_amount), 0)).filter(Order.payment_status == "paid").scalar()
    pending = Order.query.filter_by(payment_status="pending").count()
    clicks = AffiliateClick.query.count()
    products = Product.query.count()
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(6).all()
    return render_template("admin/dashboard.html", revenue=revenue, pending=pending, clicks=clicks, products=products, recent_orders=recent_orders)


def parse_product_form(form, product=None):
    limits = {
        "title": 180,
        "description": 12000,
        "category": 80,
        "price": 32,
        "compare_at_price": 32,
        "stock_quantity": 10,
        "affiliate_url": 2048,
        "aliexpress_item_id": 80,
        "aliexpress_product_id": 80,
        "aliexpress_sku_attr": 255,
        "aliexpress_source_url": 2048,
        "aliexpress_stock_status": 80,
        "supplier_price": 32,
        "commission_rate": 32,
        "images": 8 * 2048 + 7,
        "is_affiliate": 1,
        "is_featured": 1,
        "is_active": 1,
    }
    if any(len(form.get(key, "")) > maximum for key, maximum in limits.items()):
        raise ValueError("One or more product fields exceed the allowed length.")
    title = " ".join((form.get("title") or "").split())
    description = (form.get("description") or "").strip()
    category = (form.get("category") or "").strip()
    allowed = {"Home & Living", "Smart Lighting", "Workspace", "Kitchen & Coffee"}
    if not title or not description or category not in allowed:
        raise ValueError("Enter a title, description, and one of the listed categories.")
    try:
        price = Decimal(form.get("price", ""))
        compare = Decimal(form["compare_at_price"]) if form.get("compare_at_price", "").strip() else None
        stock = int(form.get("stock_quantity", "0"))
    except (InvalidOperation, ValueError):
        raise ValueError("Price and stock must be valid numbers.")
    if not price.is_finite() or (compare is not None and not compare.is_finite()) or price <= 0 or price > 1000000 or (compare is not None and compare < price) or stock < 0 or stock > 1000000:
        raise ValueError("Price must be positive, compare price must be higher, and stock cannot be negative.")
    affiliate = form.get("is_affiliate") == "1"
    affiliate_url = (form.get("affiliate_url") or "").strip() or None
    item_id = (form.get("aliexpress_item_id") or "").strip() or None
    aliexpress_product_id = (form.get("aliexpress_product_id") or item_id or "").strip() or None
    aliexpress_sku_attr = (form.get("aliexpress_sku_attr") or "").strip() or None
    aliexpress_source_url = (form.get("aliexpress_source_url") or "").strip() or None
    aliexpress_stock_status = (form.get("aliexpress_stock_status") or "").strip() or None
    supplier_price_raw = (form.get("supplier_price") or "").strip()
    try:
        supplier_price = Decimal(supplier_price_raw) if supplier_price_raw else None
    except InvalidOperation as exc:
        raise ValueError("Supplier price must be a valid amount.") from exc
    if supplier_price is not None and (not supplier_price.is_finite() or supplier_price < 0 or supplier_price > 1000000):
        raise ValueError("Supplier price must be between 0 and 1,000,000.")
    if aliexpress_source_url:
        try:
            source = urlparse(aliexpress_source_url)
        except ValueError as exc:
            raise ValueError("AliExpress source URL is invalid.") from exc
        host = (source.hostname or "").lower()
        if source.scheme != "https" or not (host == "aliexpress.com" or host.endswith(".aliexpress.com")):
            raise ValueError("AliExpress source URL must use HTTPS and point to AliExpress.")
    if affiliate:
        if not affiliate_url:
            raise ValueError("Affiliate products need an AliExpress product URL.")
        try:
            parsed = urlparse(affiliate_url)
        except ValueError as exc:
            raise ValueError("Affiliate URL is invalid.") from exc
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (hostname == "aliexpress.com" or hostname.endswith(".aliexpress.com")):
            raise ValueError("Affiliate URLs must point to AliExpress over HTTPS.")
        link = client_from_config(current_app.config)
        unchanged_existing_url = bool(
            product
            and product.is_affiliate
            and product.affiliate_url
            and affiliate_url == product.affiliate_url
        )
        already_converted = unchanged_existing_url or _is_aliexpress_tracking_link(affiliate_url)
        if link.configured and not already_converted:
            try:
                affiliate_url = link.generate_tracking_link(affiliate_url)
            except (AliExpressAPIError, ValueError) as exc:
                raise ValueError(str(exc)) from exc
    images = []
    for raw in (form.get("images") or "").splitlines():
        url = raw.strip()
        if not url:
            continue
        try:
            image_url = urlparse(url)
        except ValueError as exc:
            raise ValueError("Every image needs to be a valid https URL.") from exc
        if (
            image_url.scheme != "https"
            or not image_url.hostname
            or len(url) > 2048
        ):
            raise ValueError("Every image needs to be an https URL.")
        images.append(url)
    if len(images) > 8:
        raise ValueError("Add no more than 8 images.")
    if product is None:
        product = Product(title=title, description=description, category=category)
    product.title, product.description, product.category = title, description, category
    product.price, product.compare_at_price, product.stock_quantity = price, compare, stock
    product.is_affiliate, product.affiliate_url, product.aliexpress_item_id = affiliate, affiliate_url, item_id
    product.aliexpress_product_id = aliexpress_product_id
    product.aliexpress_sku_attr = aliexpress_sku_attr
    product.aliexpress_source_url = aliexpress_source_url
    product.aliexpress_stock_status = aliexpress_stock_status
    product.supplier_price = supplier_price
    try:
        rate = float(form.get("commission_rate", "0") or 0)
    except ValueError:
        raise ValueError("Commission rate must be a number.")
    if not math.isfinite(rate) or not 0 <= rate <= 100:
        raise ValueError("Commission rate must be between 0 and 100.")
    product.commission_rate = rate
    product.images = images
    product.is_featured = form.get("is_featured") == "1"
    product.is_active = form.get("is_active") == "1"
    if not product.slug:
        base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:170] or secrets.token_hex(6)
        slug = base
        suffix = 2
        while Product.query.filter_by(slug=slug).first():
            slug = f"{base}-{suffix}"
            suffix += 1
        product.slug = slug
    if not product.sku:
        product.sku = f"AN-{secrets.token_hex(5).upper()}"
    return product


@admin_bp.route("/products", methods=["GET", "POST"])
@admin_required
def products_manage():
    if request.method == "POST":
        try:
            product = parse_product_form(request.form)
            db.session.add(product)
            db.session.commit()
            flash("Product saved.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return render_template("admin/products_manage.html", products=Product.query.order_by(Product.created_at.desc()).all(), edit_product=None), 400
        return redirect(url_for("admin.products_manage"))
    return render_template("admin/products_manage.html", products=Product.query.order_by(Product.created_at.desc()).all(), edit_product=None)


@admin_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@admin_required
def product_edit(product_id):
    product = Product.query.get_or_404(product_id)
    if request.method == "POST":
        try:
            product = parse_product_form(request.form, product)
            db.session.commit()
            flash("Product updated.", "success")
            return redirect(url_for("admin.products_manage"))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
    return render_template("admin/products_manage.html", products=Product.query.order_by(Product.created_at.desc()).all(), edit_product=product)


@admin_bp.post("/products/<int:product_id>/delete")
@admin_required
def product_delete(product_id):
    product = Product.query.get_or_404(product_id)
    if product.order_items:
        product.is_active = False
        db.session.commit()
        flash("This product appears in an order, so it was archived.", "success")
    else:
        db.session.delete(product)
        db.session.commit()
        flash("Product removed.", "success")
    return redirect(url_for("admin.products_manage"))


@admin_bp.post("/products/<int:product_id>/sync")
@admin_required
@persistent_limit(10, 60)
def product_sync(product_id):
    product = Product.query.get_or_404(product_id)
    ali_product_id = product.aliexpress_product_id or product.aliexpress_item_id
    if not product.is_affiliate or not ali_product_id:
        flash("Only affiliate items with an AliExpress item ID can sync.", "error")
        return redirect(url_for("admin.products_manage"))
    try:
        latest = client_from_config(current_app.config).sync_product(ali_product_id)
    except AliExpressAPIError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.products_manage"))
    if not latest or latest["price"] <= 0:
        flash("AliExpress did not return a current listing for this item.", "error")
        return redirect(url_for("admin.products_manage"))
    details = latest.get("details") or {}
    product.title = details.get("title") or product.title
    product.price = Decimal(str(latest["price"]))
    product.supplier_price = Decimal(str(latest["price"]))
    product.commission_rate = latest.get("commission_rate", product.commission_rate)
    product.affiliate_url = latest["url"] or product.affiliate_url
    product.aliexpress_source_url = details.get("url") or product.aliexpress_source_url
    product.aliexpress_stock_status = details.get("stock_status") or product.aliexpress_stock_status
    if details.get("original_price") and details["original_price"] > details.get("price", 0):
        product.compare_at_price = details["original_price"]
    if details.get("images"):
        product.images = details["images"][:8]
    elif latest["image"].startswith("https://"):
        product.images = [latest["image"], *(product.images or [])[1:7]]
    if latest.get("stock_quantity") is not None:
        try:
            product.stock_quantity = max(0, int(latest["stock_quantity"]))
        except (TypeError, ValueError):
            pass
    db.session.commit()
    flash("AliExpress listing details synced. Confirm live stock manually before publishing.", "success")
    return redirect(url_for("admin.products_manage"))


@admin_bp.get("/aliexpress/connect")
@admin_required
def aliexpress_connect():
    client = client_from_config(current_app.config)
    if not client.dropshipping_configured:
        flash("Set the Dropshipping app key, secret, and registered callback URL first.", "error")
        return redirect(url_for("admin.settings"))
    expected_callback = url_for("admin.aliexpress_callback", _external=True)
    if client.ds_redirect_uri.rstrip("/") != expected_callback.rstrip("/"):
        flash("The configured AliExpress callback URL must exactly match this site's /admin/aliexpress/callback address.", "error")
        return redirect(url_for("admin.settings"))
    state = secrets.token_urlsafe(32)
    session["aliexpress_oauth_state"] = {"value": state, "created_at": int(datetime.now(timezone.utc).timestamp())}
    query = urlencode({
        "response_type": "code",
        "force_auth": "true",
        "redirect_uri": client.ds_redirect_uri,
        "client_id": client.ds_app_key,
        "state": state,
    })
    return redirect(f"https://api-sg.aliexpress.com/oauth/authorize?{query}", code=302)


@admin_bp.get("/aliexpress/callback")
@admin_required
def aliexpress_callback():
    state_record = session.pop("aliexpress_oauth_state", None)
    callback_state = request.args.get("state", "")
    if (
        not isinstance(state_record, dict)
        or not isinstance(state_record.get("value"), str)
        or not callback_state
        or len(callback_state) > 256
        or not secrets.compare_digest(state_record["value"], callback_state)
    ):
        flash("AliExpress authorization could not be verified. Please connect again.", "error")
        return redirect(url_for("admin.settings"))
    try:
        created_at = int(state_record.get("created_at", 0))
    except (TypeError, ValueError):
        created_at = 0
    age = int(datetime.now(timezone.utc).timestamp()) - created_at
    if not 0 <= age <= 600:
        flash("AliExpress authorization expired. Please connect again.", "error")
        return redirect(url_for("admin.settings"))
    if request.args.get("error"):
        flash("AliExpress authorization was declined.", "error")
        return redirect(url_for("admin.settings"))
    code = request.args.get("code", "").strip()
    if not code or len(code) > 2048:
        flash("AliExpress did not return an authorization code.", "error")
        return redirect(url_for("admin.settings"))

    try:
        token_data = client_from_config(current_app.config).exchange_authorization_code(code)
        token = db.session.get(AliExpressToken, AliExpressOpenPlatformClient.TOKEN_ID)
        if token is None:
            token = AliExpressToken(id=AliExpressOpenPlatformClient.TOKEN_ID)
            db.session.add(token)
        token.access_token = token_data["access_token"]
        token.refresh_token = token_data["refresh_token"]
        token.expires_at = token_data["expires_at"]
        token.refresh_expires_at = token_data["refresh_expires_at"]
        token.account_user_id = token_data["account_user_id"]
        token.updated_at = datetime.now(timezone.utc)
        db.session.commit()
    except (AliExpressAPIError, ValueError) as exc:
        db.session.rollback()
        current_app.logger.exception("AliExpress OAuth token exchange failed")
        flash(f"AliExpress connection failed: {exc}", "error")
        return redirect(url_for("admin.settings"))
    flash("AliExpress Dropshipping account connected.", "success")
    return redirect(url_for("admin.dashboard"))


def _product_id_from_input(value):
    value = (value or "").strip()
    if len(value) > 2048:
        raise ValueError("AliExpress product ID or URL is too long.")
    if value.isdigit() and 5 <= len(value) <= 32:
        return value, None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "aliexpress.com" or host.endswith(".aliexpress.com")):
        raise ValueError("Enter a numeric AliExpress product ID or an HTTPS AliExpress product URL.")
    match = re.search(r"/item/(\d+)(?:\.html)?", parsed.path)
    if not match:
        query = parse_qs(parsed.query)
        item_id = next(iter(query.get("itemId", [])), "")
        if not item_id.isdigit():
            raise ValueError("Could not read the product ID from that URL.")
    else:
        item_id = match.group(1)
    return item_id, value


@admin_bp.post("/aliexpress/import-product")
@admin_required
@persistent_limit(10, 60)
def aliexpress_import_product():
    raw_id = request.form.get("product_id") or request.form.get("product_url") or ""
    try:
        product_id, source_url = _product_id_from_input(raw_id)
        client = client_from_config(current_app.config)
        details = client.get_affiliate_product(product_id)
        source_url = details["url"] or source_url
        if not source_url:
            source_url = f"https://www.aliexpress.com/item/{product_id}.html"
        affiliate_url = details["affiliate_url"] or client.generate_tracking_link(source_url)
        if not details["title"] or not details["price"]:
            raise AliExpressAPIError("AliExpress product details did not include a title and sale price.")

        record_id = request.form.get("product_record_id", "").strip()
        if record_id and not record_id.isdigit():
            raise ValueError("Choose a valid existing catalog product to update.")
        product = Product.query.get(int(record_id)) if record_id else None
        if record_id and product is None:
            raise ValueError("The selected catalog product no longer exists.")
        creating_product = product is None
        if product is None:
            product = Product(
                title=details["title"],
                slug="",
                description=details["title"],
                price=details["price"],
                sku="",
                category="Home & Living",
                is_affiliate=True,
                is_active=False,
            )
        product.title = details["title"]
        product.supplier_price = details["price"]
        if product.is_affiliate or creating_product:
            product.price = details["price"]
            product.compare_at_price = details["original_price"] if details["original_price"] and details["original_price"] > details["price"] else None
        if creating_product:
            product.is_affiliate = True
            product.is_active = False
        if product.is_affiliate:
            product.affiliate_url = affiliate_url
        product.aliexpress_item_id = product_id
        product.aliexpress_product_id = product_id
        product.aliexpress_source_url = source_url
        product.aliexpress_stock_status = details["stock_status"] or None
        product.images = details["images"][:8]
        if product.is_affiliate and details["stock_quantity"] is not None:
            product.stock_quantity = details["stock_quantity"]
        if not product.slug:
            base = re.sub(r"[^a-z0-9]+", "-", product.title.lower()).strip("-")[:170] or secrets.token_hex(6)
            product.slug = f"{base}-{secrets.token_hex(3)}"
        if not product.sku:
            product.sku = f"AN-{secrets.token_hex(5).upper()}"
        db.session.add(product)
        db.session.commit()
        flash(f"AliExpress product {product_id} imported as an inactive affiliate draft. Stock status: {details['stock_status']}.", "success")
    except (ValueError, AliExpressAPIError) as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("admin.import_products"))


@admin_bp.get("/orders")
@admin_required
def orders_manage():
    orders = Order.query.order_by(Order.created_at.desc()).limit(200).all()
    return render_template("admin/orders_manage.html", orders=orders)


@admin_bp.get("/orders/<int:order_id>/packing-slip")
@admin_required
def packing_slip(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("admin/packing_slip.html", order=order)


def _sync_order_tracking(order, client):
    order_ids = [value.strip() for value in (order.aliexpress_order_id or "").split(",") if value.strip()]
    if not order_ids:
        raise AliExpressAPIError("This order has no AliExpress order ID to synchronize.")

    tracking_values = []
    services = []
    status_values = []
    for external_id in order_ids:
        details = client.get_dropshipping_order(external_id)
        number, service, status = client.extract_tracking(details)
        if number:
            tracking_values.append(number)
        if service:
            services.append(service)
        if status:
            status_values.append(status.upper())
        if number:
            try:
                tracking_data = client.get_dropshipping_tracking(
                    logistics_number=number,
                    order_id=external_id,
                    service_name=service,
                    country=order.country,
                )
                track_number, track_service, track_status = client.extract_tracking(tracking_data)
                if track_number:
                    tracking_values.append(track_number)
                if track_service:
                    services.append(track_service)
                if track_status:
                    status_values.append(track_status.upper())
            except AliExpressAPIError as exc:
                current_app.logger.warning(
                    "AliExpress tracking event lookup failed order_id=%s reason=%s",
                    order.id,
                    exc,
                )

    if tracking_values:
        order.tracking_number = ", ".join(dict.fromkeys(tracking_values))[:100]
    if services:
        order.logistics_service = ", ".join(dict.fromkeys(services))[:120]

    status_blob = " ".join(status_values)
    if any(value in status_blob for value in ("DELIVERED", "DELIVERY_SUCCESS", "FINISHED", "COMPLETED")):
        order.fulfillment_status = "delivered"
    elif any(value in status_blob for value in ("SHIPP", "TRANSIT", "PICKUP", "OUT_FOR_DELIVERY", "SELLER_SEND_GOODS")):
        order.fulfillment_status = "shipped"
    elif order.fulfillment_status not in {"shipped", "delivered"}:
        order.fulfillment_status = "placed"
    order.fulfillment_error = None


@admin_bp.post("/orders/<int:order_id>/fulfill-aliexpress")
@admin_required
@persistent_limit(10, 60)
def fulfill_order_aliexpress(order_id):
    order = Order.query.get_or_404(order_id)
    if order.aliexpress_order_id or order.fulfillment_status in {"placed", "shipped", "delivered"}:
        flash("This order has already been submitted to AliExpress.", "error")
        return redirect(url_for("admin.orders_manage"))
    if order.payment_status == "cancelled":
        flash("Cancelled orders cannot be fulfilled.", "error")
        return redirect(url_for("admin.orders_manage"))
    if order.payment_provider == "stripe" and order.payment_status != "paid":
        flash("Wait for Stripe to confirm payment before fulfilling this order.", "error")
        return redirect(url_for("admin.orders_manage"))

    logistics_service = (request.form.get("logistics_service") or "").strip()
    if len(logistics_service) > 120:
        flash("The logistics service name is too long.", "error")
        return redirect(url_for("admin.orders_manage"))
    try:
        external_ids = client_from_config(current_app.config).place_dropshipping_order(
            order,
            logistics_service_name=logistics_service
            or current_app.config.get("ALIEXPRESS_DS_LOGISTICS_SERVICE_NAME", ""),
        )
        order.aliexpress_order_id = ",".join(external_ids)[:255]
        order.fulfillment_status = "placed"
        order.fulfillment_error = None
        db.session.commit()
        flash(f"Order sent to AliExpress ({', '.join(external_ids)}).", "success")
    except (AliExpressAPIError, ValueError) as exc:
        db.session.rollback()
        order = Order.query.get(order_id)
        order.fulfillment_status = "failed"
        order.fulfillment_error = str(exc)[:4000]
        db.session.commit()
        current_app.logger.exception("AliExpress fulfillment failed for local order %s", order_id)
        flash("AliExpress fulfillment failed. Review the order error and product mappings.", "error")
    return redirect(url_for("admin.orders_manage"))


@admin_bp.post("/orders/<int:order_id>/sync-tracking")
@admin_required
@persistent_limit(30, 60)
def sync_order_tracking(order_id):
    order = Order.query.get_or_404(order_id)
    try:
        _sync_order_tracking(order, client_from_config(current_app.config))
        db.session.commit()
        flash("AliExpress tracking information updated.", "success")
    except AliExpressAPIError as exc:
        db.session.rollback()
        order = Order.query.get(order_id)
        order.fulfillment_error = str(exc)[:4000]
        db.session.commit()
        flash("AliExpress tracking could not be updated. Review the integration credentials and order ID.", "error")
    return redirect(url_for("admin.orders_manage"))


@admin_bp.post("/aliexpress/sync-all-tracking")
@admin_required
@persistent_limit(3, 60)
def sync_all_aliexpress_tracking():
    client = client_from_config(current_app.config)
    orders = (
        Order.query.filter(
            Order.aliexpress_order_id.isnot(None),
            Order.fulfillment_status.in_(("placed", "shipped")),
        )
        .order_by(Order.created_at.asc())
        .limit(100)
        .all()
    )
    updated = 0
    failed = 0
    for order in orders:
        try:
            _sync_order_tracking(order, client)
            db.session.commit()
            updated += 1
        except AliExpressAPIError as exc:
            db.session.rollback()
            order = db.session.get(Order, order.id)
            order.fulfillment_error = str(exc)[:4000]
            db.session.commit()
            failed += 1
    flash(f"Tracking sync finished: {updated} updated, {failed} failed.", "success" if failed == 0 else "error")
    return redirect(url_for("admin.orders_manage"))


@admin_bp.post("/orders/<int:order_id>/status")
@admin_required
def order_status(order_id):
    order = Order.query.get_or_404(order_id)
    status = request.form.get("status", "")
    tracking = " ".join((request.form.get("tracking_number") or "").split())
    if len(tracking) > 100:
        flash("Tracking numbers must be 100 characters or fewer.", "error")
        return redirect(url_for("admin.orders_manage"))
    if status not in {"pending", "paid", "shipped", "delivered", "cancelled"}:
        abort(400)
    if status == "shipped":
        if order.payment_status not in {"paid", "pending"}:
            flash("Cancelled orders cannot ship.", "error")
            return redirect(url_for("admin.orders_manage"))
        if order.payment_provider == "stripe" and order.payment_status != "paid":
            flash("Wait for Stripe to confirm payment before shipping this order.", "error")
            return redirect(url_for("admin.orders_manage"))
        order.fulfillment_status = "shipped"
        order.tracking_number = tracking or None
    elif status == "delivered":
        if order.fulfillment_status != "shipped":
            flash("Mark the order as shipped before marking it delivered.", "error")
            return redirect(url_for("admin.orders_manage"))
        if order.payment_provider == "stripe" and order.payment_status != "paid":
            flash("Wait for Stripe to confirm payment before marking this order delivered.", "error")
            return redirect(url_for("admin.orders_manage"))
        order.fulfillment_status = "delivered"
        order.tracking_number = tracking or order.tracking_number
    elif status == "cancelled":
        if order.payment_provider == "stripe" and order.payment_status == "pending":
            flash("This card session must expire before its reserved stock can be released.", "error")
            return redirect(url_for("admin.orders_manage"))
        if order.payment_status == "pending" and order.fulfillment_status != "shipped":
            for item in order.items:
                item.product.stock_quantity += item.quantity
            order.payment_status = "cancelled"
            order.fulfillment_status = "cancelled"
        else:
            flash("A paid order needs a refund before it can be cancelled here.", "error")
            return redirect(url_for("admin.orders_manage"))
    else:
        if status == "paid" and order.payment_provider == "stripe" and order.payment_status != "paid":
            flash("Stripe payments are confirmed by the signed webhook.", "error")
            return redirect(url_for("admin.orders_manage"))
        if status == "pending" and order.payment_status != "pending":
            flash("Payment status cannot be reset after it changes.", "error")
            return redirect(url_for("admin.orders_manage"))
        order.payment_status = status
    db.session.commit()
    flash("Order updated.", "success")
    return redirect(url_for("admin.orders_manage"))


@admin_bp.get("/settings")
@admin_required
def settings():
    api = client_from_config(current_app.config)
    dropshipping_token = db.session.get(AliExpressToken, AliExpressOpenPlatformClient.TOKEN_ID)
    stripe_ready = bool(current_app.config.get("STRIPE_SECRET_KEY") and current_app.config.get("STRIPE_WEBHOOK_SECRET"))
    return render_template(
        "admin/settings.html",
        api_configured=api.affiliate_configured,
        dropshipping_configured=api.dropshipping_configured,
        dropshipping_authorized=bool(dropshipping_token),
        stripe_configured=stripe_ready,
        mail_configured=bool(current_app.config.get("MAIL_SERVER")),
    )


@admin_bp.route("/import", methods=["GET", "POST"])
@admin_required
@persistent_limit(20, 60, methods=("POST",))
def import_products():
    results = []
    if request.method == "POST":
        client = client_from_config(current_app.config)
        keyword = " ".join((request.form.get("keyword") or "").split())
        try:
            minimum = float(request.form.get("min_commission", "0") or 0)
            category = request.form.get("category_id", "").strip() or None
            if len(keyword) > 100 or (category and (
                len(category) > 30 or not re.fullmatch(r"[A-Za-z0-9_-]+", category)
            )):
                raise ValueError("Search phrase or category ID is invalid.")
            if not math.isfinite(minimum) or not 0 <= minimum <= 100:
                raise ValueError("Minimum commission must be between 0 and 100.")
            results = (
                client.search_hot_products(category_id=category, min_commission=minimum)
                if request.form.get("search_mode") == "hot"
                else client.search_products(keyword, category_id=category, min_commission=minimum)
            )
        except (ValueError, AliExpressAPIError) as exc:
            flash(str(exc), "error")
    products = Product.query.order_by(Product.title.asc()).all()
    return render_template("admin/import_products.html", results=results, products=products)


@admin_bp.post("/import/create")
@admin_required
@persistent_limit(20, 60)
def create_imported_product():
    title = " ".join((request.form.get("title") or "").split())
    url = request.form.get("url", "").strip()
    price = request.form.get("price", "")
    try:
        source_url = request.form.get("source_url", "").strip()
        item_id = request.form.get("item_id", "").strip()
        stock_status = request.form.get("stock_status", "").strip()
        image = request.form.get("image", "").strip()
        if (
            not title
            or len(title) > 180
            or len(url) > 2048
            or len(price) > 32
            or len(source_url) > 2048
            or len(item_id) > 80
            or len(stock_status) > 80
            or len(image) > 2048
            or len(request.form.get("commission_rate", "0")) > 32
        ):
            raise ValueError("One or more imported product fields are invalid.")
        affiliate = urlparse(url)
        source = urlparse(source_url) if source_url else None
        image_url = urlparse(image) if image else None
        def allowed_host(parsed):
            hostname = (parsed.hostname or "").lower() if parsed else ""
            return bool(
                parsed
                and parsed.scheme == "https"
                and (
                    hostname == "aliexpress.com"
                    or hostname.endswith(".aliexpress.com")
                )
            )

        if not allowed_host(affiliate) or (source and not allowed_host(source)):
            raise ValueError("Imported product URLs must use HTTPS and point to AliExpress.")
        if image_url and (image_url.scheme != "https" or not image_url.hostname):
            raise ValueError("Product images must use a valid HTTPS URL.")
        commission = float(request.form.get("commission_rate", "0") or 0)
        if not math.isfinite(commission) or not 0 <= commission <= 100:
            raise ValueError("Commission rate must be between 0 and 100.")
        amount = Decimal(price)
        if not amount.is_finite() or amount <= 0 or amount > 1000000:
            raise ValueError("The imported product price is invalid.")
        product = Product(
            title=title, slug="", description=title, price=amount, sku="",
            is_affiliate=True, affiliate_url=url, aliexpress_item_id=item_id or None,
            aliexpress_product_id=item_id or None,
            aliexpress_source_url=source_url or None,
            aliexpress_stock_status=stock_status or None,
            supplier_price=amount,
            commission_rate=commission, category="Home & Living",
            images=[image] if image_url else [],
            is_active=False,
        )
        base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:170]
        product.slug = f"{base}-{secrets.token_hex(3)}"
        product.sku = f"AN-{secrets.token_hex(5).upper()}"
        db.session.add(product)
        db.session.commit()
        flash("Imported product saved as an inactive draft. Review price, image, destination link and stock before publishing.", "success")
    except (ValueError, InvalidOperation) as exc:
        db.session.rollback()
        flash("Could not save this result: " + str(exc), "error")
    return redirect(url_for("admin.products_manage"))
