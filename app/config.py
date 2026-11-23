import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )


class Config:
    IS_PRODUCTION = os.getenv("FLASK_ENV", "").lower() == "production" or bool(
        os.getenv("VERCEL")
    )
    SECRET_KEY = os.getenv("SECRET_KEY") or ""
    SQLALCHEMY_DATABASE_URI = DATABASE_URL or (
        f"sqlite:///{BASE_DIR / 'instance' / 'database.sqlite'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    ADMIN_BOOTSTRAP_KEY = os.getenv("ADMIN_BOOTSTRAP_KEY", "")
    STORE_CURRENCY = os.getenv("STORE_CURRENCY", "USD").upper()
    STORE_NAME = os.getenv("STORE_NAME", "Aesthetic Nest")
    STORE_EMAIL = os.getenv("STORE_EMAIL", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv(
        "SESSION_COOKIE_SECURE",
        "true" if os.getenv("VERCEL") or os.getenv("FLASK_ENV") == "production" else "false",
    ).lower() == "true"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    WTF_CSRF_TIME_LIMIT = int(os.getenv("WTF_CSRF_TIME_LIMIT", "3600"))
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    ENABLE_CASH_ON_DELIVERY = os.getenv("ENABLE_CASH_ON_DELIVERY", "true").lower() == "true"
    ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY", "")
    ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")
    ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID", "")
    ALIEXPRESS_API_URL = os.getenv("ALIEXPRESS_API_URL", "https://eco.taobao.com/router/rest")
    ALIEXPRESS_SESSION = os.getenv("ALIEXPRESS_SESSION", "")
    ALIEXPRESS_DS_APP_KEY = os.getenv("ALIEXPRESS_DS_APP_KEY", "")
    ALIEXPRESS_DS_APP_SECRET = os.getenv("ALIEXPRESS_DS_APP_SECRET", "")
    ALIEXPRESS_DS_REDIRECT_URI = os.getenv("ALIEXPRESS_DS_REDIRECT_URI", "")
    ALIEXPRESS_AFFILIATE_APP_KEY = os.getenv("ALIEXPRESS_AFFILIATE_APP_KEY") or ALIEXPRESS_APP_KEY
    ALIEXPRESS_AFFILIATE_APP_SECRET = os.getenv("ALIEXPRESS_AFFILIATE_APP_SECRET") or ALIEXPRESS_APP_SECRET
    ALIEXPRESS_AFFILIATE_TRACKING_ID = os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID") or ALIEXPRESS_TRACKING_ID
    ALIEXPRESS_SYNC_URL = os.getenv("ALIEXPRESS_SYNC_URL", "https://api-sg.aliexpress.com/sync")
    ALIEXPRESS_REST_URL = os.getenv("ALIEXPRESS_REST_URL", "https://api-sg.aliexpress.com/rest")
    ALIEXPRESS_DS_LOGISTICS_SERVICE_NAME = os.getenv("ALIEXPRESS_DS_LOGISTICS_SERVICE_NAME", "")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")\n    MAIL_FROM = os.getenv("MAIL_FROM") or os.getenv("DEFAULT_SENDER") or os.getenv("MAIL_DEFAULT_SENDER", "")\n    DEFAULT_SENDER = os.getenv("DEFAULT_SENDER", "")\n    MAIL_DEFAULT_SENDER = MAIL_FROM


def validate_secret_key(secret_key):
    value = str(secret_key or "").strip()
    lowered = value.lower()
    placeholders = (
        "change-me",
        "changeme",
        "default",
        "placeholder",
        "replace-me",
        "example",
        "sample",
        "insecure",
        "development",
        "your-secret",
        "your_secret",
        "secret-key",
        "secret_key",
        "test-only",
        "test-secret",
        "dev-only",
        "dev-secret",
        "secret",
    )
    return len(value) >= 32 and not any(
        placeholder in lowered for placeholder in placeholders
    )


def validate_aliexpress_config(config):
    ds_key = config.get("ALIEXPRESS_DS_APP_KEY", "")
    ds_secret = config.get("ALIEXPRESS_DS_APP_SECRET", "")
    ds_redirect = config.get("ALIEXPRESS_DS_REDIRECT_URI", "")
    if bool(ds_key) != bool(ds_secret):
        raise RuntimeError("Set both ALIEXPRESS_DS_APP_KEY and ALIEXPRESS_DS_APP_SECRET.")
    if ds_key and ds_secret and not ds_redirect:
        raise RuntimeError("Dropshipping AliExpress credentials require ALIEXPRESS_DS_REDIRECT_URI.")
    if ds_redirect:
        parsed = urlparse(ds_redirect)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError("ALIEXPRESS_DS_REDIRECT_URI must be an absolute HTTPS URL.")

    affiliate_values = (
        config.get("ALIEXPRESS_AFFILIATE_APP_KEY", ""),
        config.get("ALIEXPRESS_AFFILIATE_APP_SECRET", ""),
        config.get("ALIEXPRESS_AFFILIATE_TRACKING_ID", ""),
    )
    if any(affiliate_values) and not all(affiliate_values):
        raise RuntimeError(
            "Affiliate integration requires ALIEXPRESS_AFFILIATE_APP_KEY, "
            "ALIEXPRESS_AFFILIATE_APP_SECRET, and ALIEXPRESS_AFFILIATE_TRACKING_ID."
        )
