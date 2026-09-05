import logging
import requests
from typing import Dict, Any, List

from .base import MarketProvider

log = logging.getLogger(__name__)


class BitpinProvider(MarketProvider):
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.bitpin.ir"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._token = None

    def _get_token(self) -> str:
        if self._token:
            return self._token
        url = f"{self.base_url}/api/v1/usr/authenticate/"
        resp = requests.post(url, json={"api_key": self.api_key, "secret_key": self.api_secret}, timeout=10)
        if resp.status_code == 200:
            self._token = resp.json().get("access")
            return self._token
        raise Exception(f"Bitpin auth failed: {resp.text}")

    def _request(self, endpoint: str, params: dict = None) -> dict:
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        url = f"{self.base_url}{endpoint}"
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            log.warning(f"Bitpin request failed: {resp.status_code}")
            return {}
        return resp.json()

    def get_price(self, symbol: str) -> float:
        """
        ===== اصلاح مهم: باگ فرمت نماد =====
        قبلاً این متد مستقیماً symbol خام (مثلاً "BTC") رو به عنوان پارامتر
        ticker می‌فرستاد، در حالی که بازارهای بیت‌پین با جفت‌ارز کار می‌کنن
        (مثلاً "BTC_USDT"). یعنی این تابع همیشه نتیجه‌ی خالی می‌گرفت، همیشه
        شکست می‌خورد و به provider بعدی (که کندتره) fallback می‌کرد - هم
        باعث قیمت اشتباه/گمشده می‌شد و هم هر بار یه درخواست شبکه‌ی اضافه و
        بی‌فایده به بیت‌پین می‌زد که کلاً هدر می‌رفت (یکی از دلایل کندی ربات).
        """
        symbol_upper = symbol.upper()
        if symbol_upper == "USDT":
            return 1.0
        market_symbol = f"{symbol_upper}_USDT"
        try:
            data = self._request("/api/v1/mkt/tickers/", params={"symbol": market_symbol})
            if data and isinstance(data, list) and len(data) > 0:
                return float(data[0].get("price", 0))
            return 0.0
        except Exception as e:
            log.error(f"Bitpin price error for {symbol}: {e}")
            return 0.0

    def get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        return []

    def get_market_overview(self) -> Dict[str, Any]:
        try:
            data = self._request("/api/v1/mkt/tickers/")
            return {"provider": "bitpin", "data": data}
        except Exception as e:
            return {"provider": "bitpin", "error": str(e)}

    def supports_symbol(self, symbol: str) -> bool:
        supported = ["BTC", "ETH", "USDT", "BNB", "XRP", "ADA", "DOGE", "SHIB"]
        return symbol.upper() in supported
