import logging
import time
import requests
from app.bitpin.auth import BitpinAuth

log = logging.getLogger(__name__)

class BitpinClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.auth = BitpinAuth(base_url, api_key, api_secret)

    def _request(self, method: str, path: str, auth_required: bool = False, params: dict = None, json_body: dict = None, max_retries: int = 3):
        url = self.base_url + path
        headers = {}
        if auth_required:
            headers.update(self.auth.auth_header(self.session))

        last_exc = None
        for attempt in range(max_retries):
            try:
                resp = self.session.request(method, url, params=params, json=json_body, headers=headers, timeout=self.timeout)
                if resp.status_code >= 400:
                    raise Exception(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except Exception as e:
                last_exc = e
                wait = 2 ** attempt
                log.warning(f"Request failed, retrying in {wait}s: {e}")
                time.sleep(wait)
        raise Exception(f"Failed {method} {path} after {max_retries} retries: {last_exc}")

    def get_ticker(self, symbol: str = None):
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v1/mkt/tickers/", params=params)

    def get_markets(self):
        """
        دریافت لیست بازارهای معاملاتی بیت‌پین.
        نکته: این اندپوینت روی الگوی get_ticker (/api/v1/mkt/...) ساخته شده،
        اما مستندات رسمی بیت‌پین رو نداشتم که مسیر رو ۱۰۰٪ تایید کنم -
        قبل از دیپلوی واقعی، این مسیر رو در برابر داکیومنت رسمی بیت‌پین چک کن.
        """
        return self._request("GET", "/api/v1/mkt/markets/")

    # متدهای دیگر (در صورت نیاز)
