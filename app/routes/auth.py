import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app import db, limiter
from app.models import User


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
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("An account with that email already exists.", "error")
            else:
                login_user(user, remember=False, fresh=True)
                flash("Your account is ready.", "success")
                return redirect(url_for("auth.account"))

    return render_template("auth/register.html")


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
            return redirect(url_for("auth.account"))
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
    return render_template("auth/account.html")
