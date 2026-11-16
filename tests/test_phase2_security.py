import re
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from app import create_app, db
from app.config import validate_secret_key
from app.models import AliExpressToken, RateLimitRecord, User
from app.routes.admin import parse_product_form
from app.routes.checkout import validate_shipping
from app.security import consume_rate_limit


class PhaseTwoSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": secrets.token_hex(32),
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "WTF_CSRF_ENABLED": True,
                "SESSION_COOKIE_SECURE": False,
            }
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def create_admin(self):
        user = User(email="owner@example.com", role="owner", is_active=True)
        user.set_password("a-strong-test-password")
        db.session.add(user)
        db.session.commit()
        return user

    def login_admin(self):
        if not User.query.filter_by(email="owner@example.com").first():
            self.create_admin()
        response = self.client.get("/admin/login")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)
        ).group(1)
        return self.client.post(
            "/admin/login",
            data={
                "email": "owner@example.com",
                "password": "a-strong-test-password",
                "csrf_token": token,
            },
        )

    def test_secret_key_validation_rejects_missing_short_and_placeholder_values(self):
        for value in ("", "short-secret", "a" * 31, "placeholder-" + "x" * 32):
            with self.subTest(value=value):
                self.assertFalse(validate_secret_key(value))
        self.assertTrue(validate_secret_key(secrets.token_hex(32)))

    def test_production_startup_rejects_invalid_secret(self):
        for value in ("", "too-short", "change-me-" + "x" * 32):
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "Production requires"):
                    create_app(
                        {
                            "IS_PRODUCTION": True,
                            "SECRET_KEY": value,
                            "SQLALCHEMY_DATABASE_URI": "sqlite://",
                        }
                    )

    def test_production_session_and_security_headers(self):
        app = create_app(
            {
                "IS_PRODUCTION": True,
                "SECRET_KEY": secrets.token_hex(32),
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
            }
        )
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(app.config["PERMANENT_SESSION_LIFETIME"], timedelta(days=7))
        response = app.test_client().get("/cart")
        self.assertIn("Strict-Transport-Security", response.headers)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("https://js.stripe.com", response.headers["Content-Security-Policy"])

    def test_global_csrf_rejects_post_without_token(self):
        response = self.client.post("/cart/update", data={})
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"CSRF", response.get_data())

    def test_login_clears_pre_auth_session_and_admin_routes_require_active_admin(self):
        self.create_admin()
        with self.client.session_transaction() as state:
            state["pre_auth_marker"] = "discard this on login"
        response = self.client.get("/admin/settings")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin/login")

        with self.client.session_transaction() as state:
            state.clear()
        login = self.login_admin()
        self.assertEqual(login.status_code, 302)
        with self.client.session_transaction() as state:
            self.assertNotIn("pre_auth_marker", state)
            self.assertNotIn("csrf_token", state)
        self.assertEqual(self.client.get("/admin/settings").status_code, 200)

    def test_deactivated_admin_is_denied_access(self):
        user = self.create_admin()
        with self.client.session_transaction() as state:
            state["_user_id"] = str(user.id)
            state["_fresh"] = True
        user.is_active = False
        db.session.commit()
        response = self.client.get("/admin/settings")
        self.assertEqual(response.status_code, 302)

    def test_admin_logout_clears_authenticated_session(self):
        self.login_admin()
        self.context.pop()
        self.context = self.app.app_context()
        self.context.push()
        response = self.client.get("/admin/settings")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            response.get_data(as_text=True),
        ).group(1)
        with self.client.session_transaction() as state:
            self.assertIn("csrf_token", state)
        response = self.client.post("/admin/logout", data={"csrf_token": token})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        with self.client.session_transaction() as state:
            self.assertNotIn("_user_id", state)
            self.assertNotIn("csrf_token", state)

    def test_checkout_and_admin_product_inputs_reject_oversized_or_invalid_values(self):
        shipping = {
            "customer_name": "A" * 161,
            "email": "buyer@example.com",
            "phone": "+1 555 123 4567",
            "address_line1": "1 Main Street",
            "city": "Example",
            "region": "Example",
            "postal_code": "12345",
            "country": "US",
        }
        with self.app.test_request_context():
            with patch("app.routes.checkout.validate_csrf"):
                self.assertIn("too long", validate_shipping(shipping))

        product_form = {
            "title": "T" * 181,
            "description": "A description",
            "category": "Home & Living",
            "price": "10.00",
            "stock_quantity": "1",
        }
        with self.assertRaisesRegex(ValueError, "exceed the allowed length"):
            parse_product_form(product_form)

    def test_oauth_state_is_session_bound_single_use_and_expires(self):
        self.login_admin()
        with self.client.session_transaction() as state:
            state["aliexpress_oauth_state"] = {
                "value": "expected-state",
                "created_at": int(datetime.now(timezone.utc).timestamp()),
            }
        api = Mock()
        with patch("app.routes.admin.client_from_config", return_value=api):
            response = self.client.get("/admin/aliexpress/callback?state=wrong")
        self.assertEqual(response.status_code, 302)
        api.exchange_authorization_code.assert_not_called()
        with self.client.session_transaction() as state:
            self.assertNotIn("aliexpress_oauth_state", state)

        with self.client.session_transaction() as state:
            state["aliexpress_oauth_state"] = {
                "value": "expired-state",
                "created_at": int(
                    (datetime.now(timezone.utc) - timedelta(minutes=11)).timestamp()
                ),
            }
        with patch("app.routes.admin.client_from_config", return_value=api):
            response = self.client.get(
                "/admin/aliexpress/callback?state=expired-state&code=unused"
            )
        self.assertEqual(response.status_code, 302)
        api.exchange_authorization_code.assert_not_called()

    def test_valid_oauth_state_is_consumed_and_exchanges_once(self):
        self.login_admin()
        now = datetime.now(timezone.utc)
        with self.client.session_transaction() as state:
            state["aliexpress_oauth_state"] = {
                "value": "one-time-state",
                "created_at": int(now.timestamp()),
            }
        api = Mock()
        api.exchange_authorization_code.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": now + timedelta(hours=1),
            "refresh_expires_at": now + timedelta(days=1),
            "account_user_id": "account",
        }
        with patch("app.routes.admin.client_from_config", return_value=api):
            response = self.client.get(
                "/admin/aliexpress/callback?state=one-time-state&code=auth-code"
            )
        self.assertEqual(response.status_code, 302)
        api.exchange_authorization_code.assert_called_once_with("auth-code")
        self.assertIsNotNone(db.session.get(AliExpressToken, 1))
        with self.client.session_transaction() as state:
            self.assertNotIn("aliexpress_oauth_state", state)

    def test_rate_limit_counter_persists_and_resets_after_window(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(consume_rate_limit("test:endpoint", 2, 60, now=start))
        self.assertTrue(
            consume_rate_limit(
                "test:endpoint", 2, 60, now=start + timedelta(seconds=1)
            )
        )
        self.assertFalse(
            consume_rate_limit(
                "test:endpoint", 2, 60, now=start + timedelta(seconds=2)
            )
        )
        row = db.session.get(RateLimitRecord, "test:endpoint")
        self.assertEqual(row.count, 3)
        self.assertTrue(
            consume_rate_limit(
                "test:endpoint", 2, 60, now=start + timedelta(seconds=61)
            )
        )
        db.session.expire_all()
        row = db.session.get(RateLimitRecord, "test:endpoint")
        self.assertEqual(row.count, 1)

    def test_stripe_webhook_skips_csrf_but_rejects_invalid_signature(self):
        self.app.config["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
        response = self.client.post(
            "/checkout/stripe-webhook",
            data=b"{}",
            headers={"Stripe-Signature": "invalid"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"CSRF", response.get_data())


if __name__ == "__main__":
    unittest.main()
