import logging
import requests
from typing import Dict, Any, List

from .base import MarketProvider

log = logging.getLogger(__name__)


class BrsapiProvider(MarketProvider):
    def __init__(self, api_url: str = "https://api.brsapi.ir/Market/Gold_Currency.php"):
        self.api_url = api_url
        self._cache = {}

    def _fetch(self) -> dict:
        try:
            resp = requests.get(self.api_url, timeout=10)
            if resp.status_code == 200:
                self._cache = resp.json()
                return self._cache
            return {}
        except Exception as e:
            log.error(f"brsapi fetch error: {e}")
            return {}

    def get_price(self, symbol: str) -> float:
        data = self._fetch()
        if not data:
            return 0.0
        mapping = {
            "GOLD": "price_gold",
            "GOLD_18": "price_gold_18",
            "USD": "price_dollar",
            "USD_IRT": "price_dollar",
            "EUR": "price_eur",
            "EUR_IRT": "price_eur",
            "GBP": "price_gbp",
            "GBP_IRT": "price_gbp",
            "SILVER": "price_silver",
            "COIN": "price_coin",
            "COIN_HALF": "price_coin_half",
            "COIN_QUARTER": "price_coin_quarter"
        }
        key = mapping.get(symbol.upper(), "")
        return float(data.get(key, 0)) if key else 0.0

    def get_market_overview(self) -> Dict[str, Any]:
        data = self._fetch()
        return {
            "provider": "brsapi",
            "gold": data.get("price_gold", 0),
            "gold_18": data.get("price_gold_18", 0),
            "dollar": data.get("price_dollar", 0),
            "eur": data.get("price_eur", 0),
            "gbp": data.get("price_gbp", 0),
            "coin": data.get("price_coin", 0)
        }

    def get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        return []

    def supports_symbol(self, symbol: str) -> bool:
        supported = ["GOLD", "GOLD_18", "USD", "USD_IRT", "EUR", "EUR_IRT",
                     "GBP", "GBP_IRT", "SILVER", "COIN", "COIN_HALF", "COIN_QUARTER"]
        return symbol.upper() in supported
