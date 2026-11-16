from functools import wraps
import secrets
from datetime import datetime, timedelta, timezone

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import db


def consume_rate_limit(key, limit, window_seconds, now=None):
    """Atomically consume one fixed-window counter in the application database."""
    now = now or datetime.now(timezone.utc)
    window_start_cutoff = now - timedelta(seconds=window_seconds)
    expires_at = now + timedelta(seconds=window_seconds)
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        insert = postgresql_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise RuntimeError(
            f"Persistent rate limiting does not support database dialect {dialect!r}."
        )

    from app.models import RateLimitRecord

    is_expired = RateLimitRecord.window_start <= window_start_cutoff
    statement = insert(RateLimitRecord).values(
        key=key,
        count=1,
        window_start=now,
        expires_at=expires_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[RateLimitRecord.key],
        set_={
            "count": case((is_expired, 1), else_=RateLimitRecord.count + 1),
            "window_start": case(
                (is_expired, now), else_=RateLimitRecord.window_start
            ),
            "expires_at": case(
                (is_expired, expires_at), else_=RateLimitRecord.expires_at
            ),
        },
    ).returning(RateLimitRecord.count)

    with db.engine.begin() as connection:
        count = connection.execute(statement).scalar_one()
        if secrets.randbelow(100) == 0:
            connection.execute(
                delete(RateLimitRecord).where(RateLimitRecord.expires_at <= now)
            )
    return count <= limit


def persistent_limit(limit, window_seconds, methods=None):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if methods and request.method not in methods:
                return view(*args, **kwargs)
            address = request.remote_addr or "unknown"
            key = f"{request.endpoint}:{address}"
            if not consume_rate_limit(key, limit, window_seconds):
                abort(429, description="Too many requests. Please try again later.")
            return view(*args, **kwargs)

        return wrapped

    return decorate


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if (
            not current_user.is_authenticated
            or not current_user.is_active
            or getattr(current_user, "role", None) not in {"owner", "admin"}
        ):
            flash("Access denied. Admin privileges required.", "error")
            return redirect(url_for("admin.login"), code=302)
        return view(*args, **kwargs)
    return wrapped
