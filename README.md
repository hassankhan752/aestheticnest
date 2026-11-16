# Aesthetic Nest

A Flask storefront with separate native-checkout and AliExpress affiliate catalogs, owner-only studio routes, click attribution, and a Stripe Checkout integration that confirms card payment only through a signed webhook.

## Start on Zorin OS

From this project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before the first run. Set a long random `SECRET_KEY` and a one-time random `ADMIN_BOOTSTRAP_KEY`. For a local HTTP run set `FLASK_ENV=development` and `SESSION_COOKIE_SECURE=false`. Keep secure cookies enabled behind HTTPS in production.

Generate secret values with `python -c 'import secrets; print(secrets.token_hex(32))'` and keep each key private.

```bash
flask --app wsgi db upgrade
python run.py
```

Open `http://127.0.0.1:5000`. The SQLite database and its tables are created at `instance/database.sqlite` by the migrations. Create your first owner account at `/admin/bootstrap`, then remove `ADMIN_BOOTSTRAP_KEY` from `.env`. Sign in to the studio at `/admin/login`; the dashboard is at `/admin/dashboard`. Customers can register and sign in at `/register` and `/login`.

For a production WSGI server, put the app behind an HTTPS reverse proxy, set a randomly generated `SECRET_KEY` of at least 32 characters in the deployment secret manager, and run:

```bash
gunicorn --workers 3 --bind 127.0.0.1:8000 wsgi:app
```

## Store setup

- Create native products in the private studio panel. A native item is purchasable only while it is published and has a positive owner-confirmed stock count. The application does not pretend to own AliExpress inventory or auto-place supplier orders.
- Add AliExpress Affiliate Open Platform credentials and the publisher tracking ID to `.env` to enable live search and tracking-link generation. Imported results are drafts and must be reviewed and published by the owner.
- Set the separate `ALIEXPRESS_DS_*` credentials and callback URL plus the `ALIEXPRESS_AFFILIATE_*` credentials to enable AliExpress product detail sync, OAuth token refresh, Dropshipping fulfillment and carrier tracking. Connect the buyer account from **Admin → Store settings** before using supplier-order actions. Map each native product to an AliExpress product ID and SKU attribute before fulfilling it.
- Configure Stripe `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY` and `STRIPE_WEBHOOK_SECRET` to expose card checkout. Point the Stripe webhook to `/checkout/stripe-webhook` and enable `checkout.session.completed` and `checkout.session.expired`. Cash on delivery is available by default and can be disabled with `ENABLE_CASH_ON_DELIVERY=false`.
- Configure SMTP values in `.env` for order email. Order placement and payment state are persisted even if email delivery is not configured; email sending failures are logged.
- Apply database migrations manually before deployment. Admin sign-in, checkout creation, AliExpress sync/import, storefront search, and affiliate-click limits use atomic PostgreSQL counters; customer auth and bootstrap limits use Flask-Limiter's configured storage.

## Catalog integrity

There is no seed catalog of invented products, supplier stock counts, prices, photos, or affiliate URLs. The storefront starts empty; populate it from products you can actually source or from current API results. This keeps live prices, attribution, inventory and shipping representations under the store owner's control.

## Security notes

All browser POST/PUT/DELETE routes use Flask-WTF CSRF protection, including same-origin fetch requests. Stripe webhooks verify their signature instead of using browser CSRF. Admin entry points require an active owner/admin account; unauthenticated access redirects to `/admin/login`. Production uses secure, HTTP-only, same-site session cookies with a seven-day lifetime, proxy-aware HTTPS handling, and security response headers. Passwords use Werkzeug scrypt hashes, checkout stock is rechecked at order placement, and payment status is not changed to paid from a browser return URL. Set `SECRET_KEY`, bootstrap key, SMTP credentials and payment/API secrets only in the server's private `.env` or secret manager.
