import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import db, limiter
from app.models import Order, User


auth_bp = Blueprint("auth", __name__)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MIN_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 1024


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("auth.account"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirmation = request.form.get("password_confirm") or ""

        if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
            flash("Enter a valid email address.", "error")
        elif not _MIN_PASSWORD_LENGTH <= len(password) <= _MAX_PASSWORD_LENGTH:
            flash("Use a password between 12 and 1024 characters.", "error")
        elif password != confirmation:
            flash("The passwords do not match.", "error")
        else:
            user = User(email=email, role="customer", is_active=True)
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.flush()
                claimed_orders = Order.query.filter(
                    Order.user_id.is_(None),
                    func.lower(Order.email) == email,
                ).update({Order.user_id: user.id}, synchronize_session=False)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("An account with that email already exists.", "error")
            else:
                login_user(user, remember=False, fresh=True)
                message = "Your account is ready."
                if claimed_orders:
                    message += f" {claimed_orders} previous order(s) are now linked to your account."
                flash(message, "success")
                return redirect(url_for("auth.tracking"))

    prefill_email = (request.args.get("email") or "").strip().lower()[:254]
    return render_template("auth/register.html", prefill_email=prefill_email)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("auth.account"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email, is_active=True).first() if len(email) <= 254 else None
        if (
            len(password) <= _MAX_PASSWORD_LENGTH
            and user
            and user.role == "customer"
            and user.check_password(password)
        ):
            login_user(user, remember=False, fresh=True)
            flash("You are signed in.", "success")
            return redirect(url_for("auth.tracking"))
        flash("Email or password was not recognized.", "error")

    return render_template("auth/login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("You are signed out.", "success")
    return redirect(url_for("main.home"))


@auth_bp.get("/account")
@login_required
def account():
    if current_user.role != "customer":
        return redirect(url_for("admin.dashboard"))
    return redirect(url_for("auth.tracking"))


@auth_bp.get("/tracking")
@login_required
def tracking():
    if current_user.role != "customer":
        return redirect(url_for("admin.dashboard"))

    orders = (
        Order.query.filter_by(user_id=current_user.id)
        .order_by(Order.created_at.desc())
        .all()
    )
    statuses = {}
    for order in orders:
        if order.payment_status == "cancelled" or order.fulfillment_status == "cancelled":
            statuses[order.id] = "Cancelled"
        elif order.fulfillment_status == "delivered":
            statuses[order.id] = "Delivered"
        elif order.fulfillment_status == "shipped":
            statuses[order.id] = "Shipped"
        elif order.payment_status == "paid":
            statuses[order.id] = "Processing"
        else:
            statuses[order.id] = "Pending"
    return render_template("shop/tracking.html", orders=orders, statuses=statuses)
