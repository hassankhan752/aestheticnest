import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.services.payment import send_order_email, send_shipping_email


class TransactionalEmailTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "RESEND_API_KEY": "re_test_key",
            "MAIL_FROM": "Aesthetic Nest <orders@example.com>",
        })
        self.context = self.app.app_context()
        self.context.push()
        self.order = Mock()
        self.order.public_id = "12345678-abcd"
        self.order.email = "buyer@example.com"
        self.order.customer_name = "Taylor"
        self.order.payment_status = "paid"
        self.order.fulfillment_status = "shipped"
        self.order.tracking_number = "TRACK-123"
        self.order.logistics_service = "Parcel Post"
        self.order.currency = "USD"
        self.order.total_amount = 29.98
        item = Mock(quantity=2, product_title="Ceramic lamp", unit_price=14.99)
        self.order.items = [item]

    def tearDown(self):
        self.context.pop()

    @patch("app.services.payment.requests.post")
    def test_order_confirmation_uses_resend(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.raise_for_status.return_value = None

        self.assertTrue(send_order_email(self.order))

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.resend.com/emails")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer re_test_key")
        self.assertEqual(kwargs["json"]["to"], ["buyer@example.com"])
        self.assertIn("Order received", kwargs["json"]["subject"])
        self.assertIn("Ceramic lamp", kwargs["json"]["text"])

    @patch("app.services.payment.requests.post")
    def test_shipping_email_includes_tracking_and_carrier(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.raise_for_status.return_value = None

        self.assertTrue(send_shipping_email(self.order))

        body = post.call_args.kwargs["json"]["text"]
        self.assertIn("TRACK-123", body)
        self.assertIn("Parcel Post", body)
        self.assertIn("on its way", body)

    @patch("app.services.payment.requests.post", side_effect=OSError("network unavailable"))
    def test_email_provider_failure_never_raises(self, post):
        self.assertFalse(send_order_email(self.order))

    @patch("app.services.payment.requests.post")
    def test_unconfigured_email_is_a_safe_noop(self, post):
        self.app.config.update(RESEND_API_KEY="", MAIL_SERVER="", MAIL_FROM="")
        self.assertFalse(send_order_email(self.order))
        post.assert_not_called()

    @patch("app.services.payment.requests.post", side_effect=OSError("Resend unavailable"))
    @patch("app.services.payment.smtplib.SMTP")
    def test_smtp_is_used_when_resend_fails(self, smtp_class, post):
        self.app.config.update(MAIL_SERVER="smtp.example.com")
        smtp = smtp_class.return_value.__enter__.return_value

        self.assertTrue(send_order_email(self.order))

        post.assert_called_once()
        smtp.send_message.assert_called_once()

    @patch("app.services.payment.smtplib.SMTP")
    def test_smtp_fallback_when_resend_is_not_configured(self, smtp_class):
        self.app.config.update(RESEND_API_KEY="", MAIL_SERVER="smtp.example.com")
        smtp = smtp_class.return_value.__enter__.return_value

        self.assertTrue(send_order_email(self.order))

        smtp.send_message.assert_called_once()
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "buyer@example.com")


if __name__ == "__main__":
    unittest.main()
