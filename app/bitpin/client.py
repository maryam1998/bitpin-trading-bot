import logging
import time
import requests
from app.bitpin.auth import BitpinAuth, BitpinAuthError

log = logging.getLogger(__name__)

class BitpinClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.auth = BitpinAuth(base_url, api_key, api_secret)

    def _request(self, method: str, path: str, auth_required: bool = False, params: dict = None, json_body: dict = None, max_retries: int = 3):
        url = self.base_url + path

        last_exc = None
        for attempt in range(max_retries):
            try:
                # ===== اصلاح: گرفتن هدر Authorization داخل حلقه‌ی retry =====
                # قبلاً auth.auth_header() فقط یک بار و بیرون از حلقه صدا زده
                # می‌شد؛ یعنی اگه توکن معتبر نبود و Login با 429 مواجه می‌شد،
                # کل _request بلافاصله و بدون هیچ retry/backoffی fail می‌شد.
                # حالا این خطا هم مثل بقیه‌ی خطاهای شبکه از همین حلقه رد
                # می‌شود، با این تفاوت که BitpinAuthError جداگانه مدیریت
                # می‌شود (پایین‌تر) تا در بازه‌ی backoff دوباره درخواست
                # شبکه‌ی جدیدی برای Login زده نشود.
                headers = {}
                if auth_required:
                    headers.update(self.auth.auth_header(self.session))

                resp = self.session.request(method, url, params=params, json=json_body, headers=headers, timeout=self.timeout)
                if resp.status_code >= 400:
                    raise Exception(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except BitpinAuthError as e:
                # ===== اصلاح: خطای 429/rate-limit روی Login نباید بلافاصله
                # با یک درخواست شبکه‌ی جدید دوباره امتحان شود. خودِ
                # BitpinAuth.get_token در این حالت اصلاً درخواست شبکه‌ای نزده
                # (fail-fast)، پس اینجا هم فقط منتظر می‌مانیم و در صورت
                # اتمام تلاش‌ها، همین خطای مشخص را بالا می‌بریم تا caller
                # (مثلاً PortfolioManager) بتواند به‌شکل graceful هندلش کند
                # و کل Portfolio با یک exception خام از کار نیفتد.
                last_exc = e
                if attempt == max_retries - 1:
                    break
                wait = 2 ** attempt
                log.warning(f"Bitpin auth rate-limited, waiting {wait}s before retry ({attempt + 1}/{max_retries}): {e}")
                time.sleep(wait)
            except Exception as e:
                last_exc = e
                wait = 2 ** attempt
                log.warning(f"Request failed, retrying in {wait}s: {e}")
                time.sleep(wait)

        if isinstance(last_exc, BitpinAuthError):
            raise last_exc
        raise Exception(f"Failed {method} {path} after {max_retries} retries: {last_exc}")

    def get_ticker(self, symbol: str = None):
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v1/mkt/tickers/", params=params)

    def get_markets(self):
        return self._request("GET", "/api/v1/mkt/markets/")

    # ================= متد جدید: ارسال سفارش واقعی =================
    def place_order(self, symbol: str, side: str, order_type: str, amount: float, price: float = None) -> dict:
        """
        ارسال سفارش واقعی به بیت‌پین
        
        Args:
            symbol: نماد بازار (مثلاً 'BTC_USDT')
            side: 'buy' یا 'sell'
            order_type: 'market' یا 'limit'
            amount: مقدار (به واحد base asset)
            price: قیمت (برای سفارش limit)
        
        Returns:
            پاسخ API شامل order_id و وضعیت سفارش
        """
        body = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "amount": str(amount),
        }
        if price and order_type == "limit":
            body["price"] = str(price)

        log.info(f"📤 Placing order: {side} {amount} {symbol} @ {price or 'market'}")
        return self._request("POST", "/api/v1/odr/orders/", auth_required=True, json_body=body)

    def get_order_status(self, order_id: str) -> dict:
        """دریافت وضعیت یک سفارش"""
        return self._request("GET", f"/api/v1/odr/orders/{order_id}/", auth_required=True)

    def cancel_order(self, order_id: str) -> dict:
        """لغو یک سفارش"""
        return self._request("DELETE", f"/api/v1/odr/orders/{order_id}/", auth_required=True)
