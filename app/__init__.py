
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, request, session
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix


db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address, default_limits=[])


def create_app(test_config=None):
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    # Vercel filesystem is read-only except for /tmp.
    # Locally, use the project's normal instance directory.
    if os.environ.get("VERCEL"):
        instance_path = "/tmp/instance"
    else:
        instance_path = str(root / "instance")

    app = Flask(
        __name__,
        instance_path=instance_path,
        instance_relative_config=True
    )
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
        x_prefix=1,
    )

    app.config.from_object("app.config.Config")

    if test_config:
        app.config.update(test_config)

    production = bool(
        not app.config.get("TESTING")
        and (
            app.config.get("IS_PRODUCTION")
            or os.getenv("FLASK_ENV", "").lower() == "production"
            or bool(os.getenv("VERCEL"))
        )
    )
    if production:
        from app.config import validate_secret_key

        if not validate_secret_key(app.config.get("SECRET_KEY")):
            raise RuntimeError(
                "Production requires a non-placeholder SECRET_KEY of at least "
                "32 characters. Set SECRET_KEY to a securely generated random value."
            )
        app.config.update(
            SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE="Lax",
            PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        )

    from app.config import validate_aliexpress_config
    validate_aliexpress_config(app.config)

    if not app.config.get("SECRET_KEY"):
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-only-secret"
        else:
            raise RuntimeError(
                "Set SECRET_KEY in .env before starting Aesthetic Nest."
            )

    # Create the instance directory.
    # On Vercel this will be /tmp/instance.
    os.makedirs(app.instance_path, exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    login_manager.login_view = "auth.login"

    limiter.init_app(app)

    # Import models and blueprints
    from app import models  # noqa: F401
    from app.routes.auth import auth_bp
    from app.routes.admin import admin_bp
    from app.routes.affiliate import affiliate_bp
    from app.routes.checkout import checkout_bp
    from app.routes.main import main_bp

    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(checkout_bp)
    app.register_blueprint(affiliate_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")

    @app.before_request
    def make_sessions_permanent():
        if production:
            session.permanent = True

    @app.context_processor
    def inject_store_settings():
        return {
            "store_name": app.config["STORE_NAME"],
            "store_currency": app.config["STORE_CURRENCY"],
            "now": lambda: datetime.now(timezone.utc),
        }

    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault(
            "X-Content-Type-Options", "nosniff"
        )
        response.headers.setdefault(
            "X-Frame-Options", "DENY"
        )
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()"
        )

        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' https: data:; "
            "style-src 'self' https://fonts.googleapis.com; "
            "script-src 'self' https://js.stripe.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
            "connect-src 'self' https://api.stripe.com https://checkout.stripe.com; "
            "frame-src https://js.stripe.com https://hooks.stripe.com "
            "https://checkout.stripe.com; "
            "form-action 'self' https://checkout.stripe.com; "
            "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
        )

        if production:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains"
            )

        return response

    return app


@login_manager.user_loader
def load_user(user_id):
    from app.models import User
    return db.session.get(User, int(user_id))

