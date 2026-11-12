"""AliExpress Open Platform client for REST OAuth and signed TOP calls."""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from flask import current_app, has_app_context


class AliExpressAPIError(RuntimeError):
    """Raised for rejected, malformed, or unreachable AliExpress requests."""


class AliExpressOpenPlatformClient:
    REQUEST_TIMEOUT = (5, 25)
    TOKEN_ID = 1
    TOKEN_REFRESH_WINDOW = timedelta(minutes=10)

    def __init__(self, config):
        self.ds_app_key = config.get("ALIEXPRESS_DS_APP_KEY", "")
        self.ds_app_secret = config.get("ALIEXPRESS_DS_APP_SECRET", "")
        self.ds_redirect_uri = config.get("ALIEXPRESS_DS_REDIRECT_URI", "")
        self.affiliate_app_key = config.get("ALIEXPRESS_AFFILIATE_APP_KEY", "")
        self.affiliate_app_secret = config.get("ALIEXPRESS_AFFILIATE_APP_SECRET", "")
        self.affiliate_tracking_id = config.get("ALIEXPRESS_AFFILIATE_TRACKING_ID", "")
        self.sync_url = config.get("ALIEXPRESS_SYNC_URL", "https://api-sg.aliexpress.com/sync")
        self.rest_url = config.get("ALIEXPRESS_REST_URL", "https://api-sg.aliexpress.com/rest")
        self.logistics_service_name = config.get("ALIEXPRESS_DS_LOGISTICS_SERVICE_NAME", "")

    @property
    def dropshipping_configured(self):
        return bool(self.ds_app_key and self.ds_app_secret and self.ds_redirect_uri)

    @property
    def affiliate_configured(self):
        return bool(
            self.affiliate_app_key
            and self.affiliate_app_secret
            and self.affiliate_tracking_id
        )

    @property
    def configured(self):
        return self.affiliate_configured

    @staticmethod
    def _timestamp():
        return str(int(time.time() * 1000))

    @staticmethod
    def _canonical_value(value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @classmethod
    def sign_rest(cls, api_path, params, app_secret):
        canonical = api_path + "".join(
            f"{key}{cls._canonical_value(params[key])}"
            for key in sorted(params)
            if key != "sign"
        )
        return hmac.new(
            app_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()

    @classmethod
    def sign_top(cls, params, app_secret):
        canonical = "".join(
            f"{key}{cls._canonical_value(params[key])}"
            for key in sorted(params)
            if key not in {"sign", "image_file_bytes"}
        )
        return hmac.new(
            app_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()

    @staticmethod
    def _logger():
        return current_app.logger if has_app_context() else logging.getLogger(__name__)

    @classmethod
    def _request_json(cls, url, params, method_name, *, json_body=False):
        try:
            if json_body:
                response = requests.post(
                    url,
                    params={key: value for key, value in params.items() if key in {"app_key", "timestamp", "sign_method", "sign"}},
                    json={key: value for key, value in params.items() if key not in {"app_key", "timestamp", "sign_method", "sign"}},
                    timeout=cls.REQUEST_TIMEOUT,
                )
            else:
                form_values = {
                    key: cls._canonical_value(value)
                    for key, value in params.items()
                }
                response = requests.post(url, data=form_values, timeout=cls.REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            cls._logger().error("AliExpress request failed method=%s reason=%s", method_name, exc)
            raise AliExpressAPIError("AliExpress could not be reached. Check its API status and configuration.") from exc

        try:
            data = response.json()
        except ValueError as exc:
            cls._logger().error(
                "AliExpress returned non-JSON response method=%s status=%s body=%r",
                method_name,
                response.status_code,
                response.text[:4000],
            )
            raise AliExpressAPIError("AliExpress returned an unexpected response format.") from exc

        error = cls._find_key(data, {"error_response"})
        if response.status_code >= 400 or error is not None or not isinstance(data, dict):
            error_obj = error if isinstance(error, dict) else data if isinstance(data, dict) else {}
            code = cls._first_value(error_obj, ("code", "error_code", "sub_code", "rsp_code"))
            message = cls._first_value(error_obj, ("msg", "message", "error_msg", "sub_msg", "rsp_msg"))
            cls._logger().error(
                "AliExpress rejected request method=%s status=%s code=%r message=%r body=%r",
                method_name,
                response.status_code,
                code,
                message,
                response.text[:4000],
            )
            raise AliExpressAPIError(
                f"AliExpress rejected {method_name}: {message or code or 'unexpected response'}."
            )
        return data

    @classmethod
    def _find_key(cls, node, names):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in names:
                    return value
            for value in node.values():
                found = cls._find_key(value, names)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = cls._find_key(value, names)
                if found is not None:
                    return found
        return None

    @classmethod
    def _find_values(cls, node, names):
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key in names and value is not None:
                    found.append(value)
                found.extend(cls._find_values(value, names))
        elif isinstance(node, list):
            for value in node:
                found.extend(cls._find_values(value, names))
        return found

    @staticmethod
    def _first_value(data, names):
        if not isinstance(data, dict):
            return None
        for name in names:
            if data.get(name) is not None:
                return data[name]
        return None

    @classmethod
    def _top_error(cls, data):
        error = cls._find_key(data, {"error_response"})
        if error is not None:
            return error
        for key in ("result", "resp_result"):
            result = cls._find_key(data, {key})
            if isinstance(result, dict):
                if result.get("is_success") is False or result.get("result_success") is False:
                    return result
                code = result.get("resp_code")
                if code not in (None, 200, "200", "00", "0"):
                    return result
        return None

    def _rest_call(self, api_path, arguments, *, access_token=None):
        if not self.ds_app_key or not self.ds_app_secret:
            raise AliExpressAPIError("AliExpress Dropshipping API credentials are not configured.")
        if not api_path.startswith("/") or ".." in api_path:
            raise ValueError("AliExpress REST path must be a local API path.")
        params = {
            "app_key": self.ds_app_key,
            "timestamp": self._timestamp(),
            "sign_method": "sha256",
            **arguments,
        }
        if access_token:
            params["access_token"] = access_token
        params["sign"] = self.sign_rest(api_path, params, self.ds_app_secret)
        return self._request_json(
            f"{self.rest_url.rstrip('/')}{api_path}", params, api_path
        )

    @classmethod
    def _response_record(cls, data):
        if not isinstance(data, dict):
            return {}
        for key in ("token_info", "data", "result", "response"):
            value = cls._find_key(data, {key})
            if isinstance(value, dict):
                if any(name in value for name in ("access_token", "refresh_token")):
                    return value
        return data

    def exchange_authorization_code(self, code):
        data = self._rest_call("/auth/token/create", {"code": code})
        token_data = self._response_record(data)
        if not token_data.get("access_token") or not token_data.get("refresh_token"):
            raise AliExpressAPIError("AliExpress did not return both access and refresh tokens.")
        now = datetime.now(timezone.utc)
        return {
            "access_token": token_data["access_token"],
            "refresh_token": token_data["refresh_token"],
            "expires_at": self._token_expiry(
                token_data,
                now,
                duration_names=("expires_in", "expiresIn"),
                absolute_names=("expire_time", "expires_at"),
                default_seconds=86400,
            ),
            "refresh_expires_at": self._token_expiry(
                token_data,
                now,
                duration_names=("refresh_expires_in", "refreshExpiresIn"),
                absolute_names=("refresh_token_valid_time", "refresh_expires_at"),
                default_seconds=172800,
            ),
            "account_user_id": str(token_data.get("account") or token_data.get("user_id") or "")[:128] or None,
        }

    @staticmethod
    def _token_expiry(data, now, *, duration_names, absolute_names, default_seconds):
        for name in duration_names:
            value = data.get(name)
            if value is not None:
                try:
                    return now + timedelta(seconds=max(0, float(value)))
                except (TypeError, ValueError, OverflowError):
                    pass
        for name in absolute_names:
            value = data.get(name)
            if value is None:
                continue
            try:
                numeric = float(value)
                if numeric >= 100_000_000_000:
                    return datetime.fromtimestamp(numeric / 1000, timezone.utc)
                if numeric >= 1_000_000_000:
                    return datetime.fromtimestamp(numeric, timezone.utc)
                return now + timedelta(seconds=max(0, numeric))
            except (TypeError, ValueError, OverflowError, OSError):
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    continue
        return now + timedelta(seconds=default_seconds)

    def _load_fresh_token(self):
        from app import db
        from app.models import AliExpressToken

        token = db.session.get(AliExpressToken, self.TOKEN_ID)
        if token is None:
            raise AliExpressAPIError("Connect the AliExpress Dropshipping account from the admin panel first.")
        now = datetime.now(timezone.utc)
        expires_at = self._aware(token.expires_at)
        if expires_at > now + self.TOKEN_REFRESH_WINDOW:
            return token
        if self._aware(token.refresh_expires_at) <= now:
            raise AliExpressAPIError("The AliExpress authorization expired. Reconnect the account in admin settings.")

        try:
            data = self._rest_call(
                "/auth/token/refresh",
                {"refresh_token": token.refresh_token},
            )
            refreshed = self._response_record(data)
            if not refreshed.get("access_token"):
                raise AliExpressAPIError("AliExpress token refresh returned no access token.")
            now = datetime.now(timezone.utc)
            token.access_token = refreshed["access_token"]
            token.refresh_token = refreshed.get("refresh_token") or token.refresh_token
            token.expires_at = self._token_expiry(
                refreshed,
                now,
                duration_names=("expires_in", "expiresIn"),
                absolute_names=("expire_time", "expires_at"),
                default_seconds=86400,
            )
            token.refresh_expires_at = self._token_expiry(
                refreshed,
                now,
                duration_names=("refresh_expires_in", "refreshExpiresIn"),
                absolute_names=("refresh_token_valid_time", "refresh_expires_at"),
                default_seconds=max(0, (self._aware(token.refresh_expires_at) - now).total_seconds()),
            )
            token.updated_at = now
            db.session.commit()
            return token
        except Exception:
            db.session.rollback()
            raise

    @staticmethod
    def _aware(value):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _top_call(self, method, arguments, *, app_key, app_secret, session_token=""):
        if not app_key or not app_secret:
            raise AliExpressAPIError("AliExpress API credentials are not configured.")
        params = {
            "app_key": app_key,
            "method": method,
            "timestamp": self._timestamp(),
            "format": "json",
            "v": "2.0",
            "sign_method": "hmac-sha256",
            **arguments,
        }
        if session_token:
            params["session"] = session_token
        params["sign"] = self.sign_top(params, app_secret)
        data = self._request_json(self.sync_url, params, method)
        error = self._top_error(data)
        if error:
            code = self._first_value(error, ("error_code", "code", "rsp_code"))
            message = self._first_value(error, ("error_msg", "msg", "rsp_msg", "sub_msg"))
            self._logger().error("AliExpress method=%s error_code=%r message=%r", method, code, message)
            raise AliExpressAPIError(f"AliExpress rejected {method}: {message or code or 'API error'}.")
        return data

    def dropshipping_call(self, method, arguments):
        token = self._load_fresh_token()
        return self._top_call(
            method,
            arguments,
            app_key=self.ds_app_key,
            app_secret=self.ds_app_secret,
            session_token=token.access_token,
        )

    def affiliate_call(self, method, arguments):
        if not self.affiliate_configured:
            raise AliExpressAPIError("AliExpress Affiliate credentials are not configured.")
        return self._top_call(
            method,
            arguments,
            app_key=self.affiliate_app_key,
            app_secret=self.affiliate_app_secret,
        )

    def _affiliate_fields(self):
        return (
            "product_id,product_title,product_detail_url,product_main_image_url,"
            "product_small_image_urls,target_sale_price,target_original_price,"
            "app_sale_price,sale_price,commission_rate,promotion_link,product_status_type"
        )

    def get_affiliate_product(self, product_id):
        data = self.affiliate_call(
            "aliexpress.affiliate.productdetail.get",
            {
                "product_ids": str(product_id),
                "fields": self._affiliate_fields(),
                "tracking_id": self.affiliate_tracking_id,
                "target_currency": "USD",
                "target_language": "EN",
                "country": "US",
            },
        )
        product = self._find_key(data, {"product"})
        if isinstance(product, list):
            product = product[0] if product else None
        elif isinstance(product, dict) and "product_id" not in product:
            product = next(iter(product.values()), None)
            if isinstance(product, list):
                product = product[0] if product else None
        if not isinstance(product, dict):
            raise AliExpressAPIError("AliExpress did not return product details for that ID.")

        images_value = product.get("product_small_image_urls") or []
        if isinstance(images_value, dict):
            images_value = images_value.get("string") or images_value.get("url") or []
        if isinstance(images_value, str):
            images_value = [images_value]
        images = [str(value) for value in images_value if isinstance(value, str)] if isinstance(images_value, list) else []
        main_image = product.get("product_main_image_url")
        if main_image:
            images.insert(0, str(main_image))
        images = list(dict.fromkeys(url.replace("http://", "https://", 1) for url in images if url.startswith(("http://", "https://"))))[:8]
        price = self._decimal_value(
            product.get("target_sale_price")
            or product.get("app_sale_price")
            or product.get("sale_price")
        )
        quantity = self._first_value(product, ("stock_quantity", "product_stock", "quantity_available", "available_stock", "inventory"))
        try:
            quantity = max(0, int(quantity)) if quantity is not None else None
        except (TypeError, ValueError):
            quantity = None
        stock_status = str(product.get("product_status_type") or "").strip()
        if not stock_status and quantity is not None:
            stock_status = "in_stock" if quantity > 0 else "out_of_stock"
        return {
            "product_id": str(product.get("product_id") or product_id),
            "title": str(product.get("product_title") or "")[:180],
            "url": str(product.get("product_detail_url") or ""),
            "affiliate_url": str(product.get("promotion_link") or ""),
            "images": images,
            "price": price,
            "original_price": self._decimal_value(product.get("target_original_price") or product.get("original_price")),
            "stock_quantity": quantity,
            "stock_status": stock_status or "unknown",
            "commission_rate": self._commission_rate(product.get("commission_rate")),
        }

    @staticmethod
    def _decimal_value(value):
        try:
            from decimal import Decimal, InvalidOperation

            number = Decimal(str(value).replace(",", ""))
            return number if number.is_finite() and number >= 0 else None
        except (InvalidOperation, TypeError, ValueError):
            return None

    @staticmethod
    def _commission_rate(value):
        try:
            return max(0.0, min(float(str(value or "0").rstrip("%")), 100.0))
        except (TypeError, ValueError):
            return 0.0

    def search_products(self, keyword, page=1, category_id=None, min_commission=0):
        arguments = {
            "keywords": " ".join(str(keyword).split())[:100],
            "page_no": max(1, int(page)),
            "page_size": 20,
            "tracking_id": self.affiliate_tracking_id,
            "target_currency": "USD",
            "target_language": "EN",
            "ship_to_country": "US",
            "fields": self._affiliate_fields(),
        }
        if category_id:
            arguments["category_ids"] = str(category_id)
        return self._normalize_affiliate_products(
            self.affiliate_call("aliexpress.affiliate.product.query", arguments), min_commission
        )

    def search_hot_products(self, page=1, category_id=None, min_commission=0):
        arguments = {
            "page_no": max(1, int(page)),
            "page_size": 20,
            "tracking_id": self.affiliate_tracking_id,
            "target_currency": "USD",
            "target_language": "EN",
            "ship_to_country": "US",
            "fields": self._affiliate_fields(),
        }
        if category_id:
            arguments["category_ids"] = str(category_id)
        return self._normalize_affiliate_products(
            self.affiliate_call("aliexpress.affiliate.hotproduct.query", arguments), min_commission
        )

    @classmethod
    def _normalize_affiliate_products(cls, data, min_commission=0):
        rows = cls._find_key(data, {"product", "product_list", "products"}) or []
        if isinstance(rows, dict):
            rows = rows.get("product") or list(rows.values())
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            return []
        output = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                rate = float(str(row.get("commission_rate") or "0").rstrip("%"))
            except (TypeError, ValueError):
                rate = 0.0
            if rate < float(min_commission or 0):
                continue
            url = str(row.get("promotion_link") or row.get("product_detail_url") or "")
            image = str(row.get("product_main_image_url") or "")
            stock_quantity = cls._first_value(row, ("stock_quantity", "product_stock", "quantity_available"))
            stock_status = str(row.get("product_status_type") or "").strip()
            if not stock_status and stock_quantity is not None:
                try:
                    stock_status = "in_stock" if int(stock_quantity) > 0 else "out_of_stock"
                except (TypeError, ValueError):
                    stock_status = "unknown"
            output.append({
                "title": str(row.get("product_title") or "")[:180],
                "item_id": str(row.get("product_id") or ""),
                "url": url.replace("http://", "https://", 1),
                "source_url": str(row.get("product_detail_url") or "").replace("http://", "https://", 1),
                "image": image.replace("http://", "https://", 1),
                "price": float(cls._decimal_value(row.get("target_sale_price") or row.get("sale_price")) or 0),
                "commission_rate": rate,
                "stock_quantity": stock_quantity,
                "stock_status": stock_status or "unknown",
            })
        return output

    def generate_tracking_link(self, product_url):
        parsed = urlparse(product_url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (hostname == "aliexpress.com" or hostname.endswith(".aliexpress.com")):
            raise ValueError("Enter an AliExpress product URL over HTTPS.")
        data = self.affiliate_call(
            "aliexpress.affiliate.link.generate",
            {
                "tracking_id": self.affiliate_tracking_id,
                "promotion_link_type": 0,
                "source_values": product_url,
            },
        )
        result = self._find_key(data, {"promotion_link", "promotion_links"})
        if isinstance(result, dict):
            result = result.get("promotion_link")
        if isinstance(result, list):
            result = result[0] if result else None
        if isinstance(result, dict):
            result = result.get("promotion_link")
        if isinstance(result, str) and result.strip():
            return result.strip().replace("http://", "https://", 1)
        raise AliExpressAPIError("AliExpress returned no affiliate link for this product.")

    def sync_product(self, product_id):
        data = self.get_affiliate_product(product_id)
        return {
            "item_id": data["product_id"],
            "url": data["affiliate_url"] or data["url"],
            "image": data["images"][0] if data["images"] else "",
            "price": float(data["price"] or 0),
            "stock_quantity": data["stock_quantity"],
            "commission_rate": data["commission_rate"],
            "details": data,
        }

    def place_dropshipping_order(self, order, logistics_service_name=""):
        line_items = []
        for item in order.items:
            product = item.product
            product_id = (product.aliexpress_product_id or product.aliexpress_item_id or "").strip()
            if product.is_affiliate:
                raise AliExpressAPIError(f"{item.product_title} is an affiliate product and cannot be fulfilled through Dropshipping.")
            if not product_id:
                raise AliExpressAPIError(f"Map {item.product_title} to an AliExpress product ID before fulfillment.")
            if not product.aliexpress_sku_attr:
                raise AliExpressAPIError(f"Set an AliExpress SKU attribute for {item.product_title} before fulfillment.")
            line = {
                "product_id": product_id,
                "sku_attr": product.aliexpress_sku_attr,
                "product_count": int(item.quantity),
            }
            if logistics_service_name:
                line["logistics_service_name"] = logistics_service_name
            line_items.append(line)

        shipping = {
            "address": order.address_line1,
            "address2": order.address_line2 or "",
            "city": order.city,
            "province": order.region,
            "country": order.country,
            "contact_person": order.customer_name,
            "full_name": order.customer_name,
            "mobile_no": order.phone,
            "phone": order.phone,
            "zip": order.postal_code,
            "email": order.email,
            "locale": "en_US",
        }
        payload = {
            "logistics_address": shipping,
            "product_items": line_items,
        }
        data = self.dropshipping_call(
            "aliexpress.trade.buy.placeorder",
            {"param_place_order_request4_open_api_d_t_o": payload},
        )
        result = self._find_key(data, {"result"})
        if not isinstance(result, dict):
            raise AliExpressAPIError("AliExpress accepted the request without returning an order result.")
        if str(result.get("is_success", "")).strip().lower() in {"false", "0", "no"}:
            raise AliExpressAPIError(str(result.get("error_msg") or result.get("error_code") or "AliExpress could not place this order."))
        order_list = result.get("order_list")
        if isinstance(order_list, dict):
            order_ids = order_list.get("number") or order_list.get("order_id") or []
        else:
            order_ids = order_list
        if isinstance(order_ids, (str, int)):
            order_ids = [order_ids]
        order_ids = [str(value) for value in (order_ids or []) if str(value).strip()]
        if not order_ids:
            raise AliExpressAPIError("AliExpress did not return an external order ID.")
        return order_ids

    def get_dropshipping_order(self, aliexpress_order_id):
        return self.dropshipping_call(
            "aliexpress.trade.ds.order.get",
            {"single_order_query": {"order_id": str(aliexpress_order_id)}},
        )

    def get_dropshipping_tracking(self, *, logistics_number, order_id, service_name="", country=""):
        try:
            return self.dropshipping_call(
                "aliexpress.ds.order.tracking.get",
                {"order_id": str(order_id)},
            )
        except AliExpressAPIError:
            if not logistics_number:
                raise
            return self.dropshipping_call(
                "aliexpress.logistics.ds.trackinginfo.query",
                {
                    "logistics_no": str(logistics_number),
                    "origin": "ESCROW",
                    "out_ref": str(order_id),
                    "service_name": service_name,
                    "to_area": country,
                },
            )

    @staticmethod
    def extract_tracking(data):
        logistics = AliExpressOpenPlatformClient._find_key(data, {"logistics_info_list", "aeop_order_logistics_info", "ae_order_logistics_info"})
        if isinstance(logistics, dict):
            logistics = logistics.get("aeop_order_logistics_info") or logistics.get("ae_order_logistics_info") or list(logistics.values())
        if isinstance(logistics, dict):
            logistics = [logistics]
        if not isinstance(logistics, list):
            logistics = []
        tracking_number = ""
        service = ""
        for entry in logistics:
            if isinstance(entry, dict):
                tracking_number = str(entry.get("logistics_no") or entry.get("tracking_number") or entry.get("international_logistics_num") or "")
                service = str(entry.get("logistics_service") or entry.get("service_name") or entry.get("logistics_service_name") or "")
                if tracking_number or service:
                    break
        status = " ".join(
            str(value).upper()
            for value in AliExpressOpenPlatformClient._find_values(
                data, {"logistics_status", "order_status", "status", "event_desc"}
            )
        )
        return tracking_number, service, status


def client_from_config(config=None):
    if config is None:
        config = current_app.config
    return AliExpressOpenPlatformClient(config)
