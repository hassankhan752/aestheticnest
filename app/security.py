from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or getattr(current_user, "role", None) not in {"owner", "admin"}:
            flash("Access denied. Admin privileges required.", "error")
            return redirect(url_for("admin.login"), code=302)
        return view(*args, **kwargs)
    return wrapped
