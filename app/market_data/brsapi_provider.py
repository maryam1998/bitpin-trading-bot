import logging
import requests
from typing import Dict, Any, List, Optional

from .base import MarketProvider

log = logging.getLogger(__name__)

# نگاشت نمادهای ما به کلیدهای احتمالی/نام‌های احتمالی که BrsApi ممکن است
# در نسخه‌های مختلف پاسخش استفاده کند. چون مستندات دقیق و پایدار عمومی
# در دسترس نبود، به‌جای فرض یک ساختار ثابت، چند شکل رایج را امتحان می‌کنیم.
SYMBOL_ALIASES = {
    "GOLD": {"price_gold", "GOLD_18", "IR_GOLD_18K", "18ayar", "geram18"},
    "GOLD_18": {"price_gold_18", "GOLD_18", "IR_GOLD_18K", "18ayar", "geram18"},
    "USD": {"price_dollar", "USD", "USD_IRT", "دلار"},
    "USD_IRT": {"price_dollar", "USD", "USD_IRT", "دلار"},
    "EUR": {"price_eur", "EUR", "یورو"},
    "EUR_IRT": {"price_eur", "EUR", "یورو"},
    "GBP": {"price_gbp", "GBP", "پوند"},
    "GBP_IRT": {"price_gbp", "GBP", "پوند"},
    "SILVER": {"price_silver", "SILVER", "نقره"},
    "COIN": {"price_coin", "COIN", "IR_COIN_EMAMI", "سکه امامی"},
    "COIN_HALF": {"price_coin_half", "COIN_HALF", "نیم سکه"},
    "COIN_QUARTER": {"price_coin_quarter", "COIN_QUARTER", "ربع سکه"},
}


class BrsapiProvider(MarketProvider):
    def __init__(self, api_url: str = "https://brsapi.ir/FreeTsetmcBourseApi/Api_Free_Gold_Currency_v2.json",
                 api_key: str = ""):
        self.api_url = api_url
        self.api_key = api_key
        self._cache = {}
        self._last_raw_logged = False

    def _fetch(self) -> dict:
        try:
            params = {"key": self.api_key} if self.api_key else None
            resp = requests.get(self.api_url, params=params, timeout=10)
            if resp.status_code == 200:
                self._cache = resp.json()
                return self._cache
            log.error(f"brsapi HTTP {resp.status_code}: {resp.text[:300]}")
            return {}
        except Exception as e:
            log.error(f"brsapi fetch error: {e}")
            return {}

    def _extract(self, symbol: str, data: dict) -> Optional[float]:
        """
        استخراج قیمت به‌صورت مقاوم در برابر چند شکل مختلف پاسخ:
        ۱) دیکشنری تخت مثل {"price_gold": 12345, ...}
        ۲) دیکشنری تو در تو با لیست‌هایی مثل {"gold": [{"symbol": "...", "price": ...}], ...}
        این باعث می‌شود اگر ساختار دقیق پاسخ BrsApi عوض شد، کد به‌جای همیشه
        صفر برگرداندن، حداقل تلاش کند مقدار درست را پیدا کند.
        """
        if not data:
            return None

        aliases = {a.upper() for a in SYMBOL_ALIASES.get(symbol.upper(), set())}

        # حالت ۱: کلید تخت
        for key in aliases:
            if key in data:
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    pass

        # حالت ۲: جستجو در لیست‌های تودرتو
        def search(node):
            if isinstance(node, dict):
                sym = str(node.get("symbol") or node.get("name") or node.get("name_en") or "").upper()
                if sym in aliases or sym.replace(" ", "") in aliases:
                    for price_key in ("price", "value", "close", "last"):
                        if price_key in node:
                            try:
                                return float(node[price_key])
                            except (TypeError, ValueError):
                                pass
                for v in node.values():
                    result = search(v)
                    if result is not None:
                        return result
            elif isinstance(node, list):
                for item in node:
                    result = search(item)
                    if result is not None:
                        return result
            return None

        return search(data)

    def get_price(self, symbol: str) -> float:
        data = self._fetch()
        if not data:
            return 0.0
        value = self._extract(symbol, data)
        if value is None:
            if not self._last_raw_logged:
                # فقط یک‌بار کل پاسخ خام رو لاگ می‌کنیم تا اگه ساختار عوض شده،
                # قابل بررسی و اصلاح باشه (به‌جای همیشه silent صفر برگردوندن)
                log.warning(f"brsapi: قیمت {symbol} پیدا نشد. نمونه پاسخ خام: {str(data)[:500]}")
                self._last_raw_logged = True
            return 0.0
        return value

    def get_market_overview(self) -> Dict[str, Any]:
        data = self._fetch()
        return {
            "provider": "brsapi",
            "gold": self._extract("GOLD", data) or 0,
            "dollar": self._extract("USD", data) or 0,
            "eur": self._extract("EUR", data) or 0,
            "gbp": self._extract("GBP", data) or 0,
            "coin": self._extract("COIN", data) or 0,
        }

    def get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        return []

    def supports_symbol(self, symbol: str) -> bool:
        supported = ["GOLD", "GOLD_18", "USD", "USD_IRT", "EUR", "EUR_IRT",
                     "GBP", "GBP_IRT", "SILVER", "COIN", "COIN_HALF", "COIN_QUARTER"]
        return symbol.upper() in supported
