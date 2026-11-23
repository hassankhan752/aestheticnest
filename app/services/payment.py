import smtplib
from email.message import EmailMessage

import requests
import stripe
from flask import current_app, url_for


RESEND_ENDPOINT = "https://api.resend.com/emails"


def stripe_checkout(order):
    if not current_app.config.get("STRIPE_SECRET_KEY"):
        return None
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    items = [{
        "price_data": {
            "currency": order.currency.lower(),
            "product_data": {"name": item.product_title, "metadata": {"sku": item.sku}},
            "unit_amount": int(item.unit_price * 100),
        },
        "quantity": item.quantity,
    } for item in order.items]
    session = stripe.checkout.Session.create(
        mode="payment", line_items=items, customer_email=order.email,
        success_url=url_for("checkout.payment_success", public_id=order.public_id, _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("checkout.payment_cancel", public_id=order.public_id, _external=True),
        client_reference_id=order.public_id, metadata={"order_id": order.public_id},
    )
    return session


def _sender(config):
    return (
        config.get("MAIL_FROM")
        or config.get("DEFAULT_SENDER")
        or config.get("MAIL_DEFAULT_SENDER")
        or ""
    )


def _send_email(recipient, subject, body):
    """Deliver through Resend first, otherwise configured SMTP; never break a store flow."""
    cfg = current_app.config
    sender = _sender(cfg)
    resend_key = cfg.get("RESEND_API_KEY")
    smtp_ready = bool(cfg.get("MAIL_SERVER") and sender)
    if not sender or not (resend_key or smtp_ready):
        current_app.logger.info("Transactional email skipped: no mail provider configured")
        return False

    try:
        if resend_key:
            response = requests.post(
                RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {resend_key}",
                    "Content-Type": "application/json",
                },
                json={"from": sender, "to": [recipient], "subject": subject, "text": body},
                timeout=12,
            )
            response.raise_for_status()
            return True

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(body)
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg.get("MAIL_PORT", 587), timeout=12) as smtp:
            if cfg.get("MAIL_USE_TLS"):
                smtp.starttls()
            if cfg.get("MAIL_USERNAME"):
                smtp.login(cfg["MAIL_USERNAME"], cfg.get("MAIL_PASSWORD", ""))
            smtp.send_message(message)
        return True
    except Exception:
        current_app.logger.exception("Transactional email delivery failed")
        return False


def send_order_email(order):
    details = "\\n".join(
        f"{item.quantity} × {item.product_title} — {order.currency} {item.unit_price:.2f}"
        for item in order.items
    )
    body = (
        f"Hello {order.customer_name},\\n\\n"
        f"We received your order {order.public_id[:8].upper()}. "
        f"Payment status: {order.payment_status}.\\n\\n{details}\\n\\n"
        f"Total: {order.currency} {order.total_amount:.2f}\\n\\n"
        "Thank you for choosing Aesthetic Nest."
    )
    return _send_email(
        order.email,
        f"Order received · {order.public_id[:8].upper()}",
        body,
    )


def send_shipping_email(order):
    status = (order.fulfillment_status or "shipped").lower()
    if status == "delivered":
        headline = "Your Aesthetic Nest order has been delivered."
        subject_status = "delivered"
    else:
        headline = "Your Aesthetic Nest order is on its way."
        subject_status = "shipped"
    tracking = order.tracking_number or "Tracking details are not available yet."
    carrier = order.logistics_service or "Carrier details will be shared when available."
    body = (
        f"Hello {order.customer_name},\\n\\n{headline}\\n"
        f"Order: {order.public_id[:8].upper()}\\n"
        f"Tracking number: {tracking}\\n"
        f"Carrier: {carrier}\\n\\n"
        "Thank you for shopping with Aesthetic Nest."
    )
    return _send_email(
        order.email,
        f"Your order has been {subject_status} · {order.public_id[:8].upper()}",
        body,
    )
