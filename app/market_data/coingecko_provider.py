import logging
import requests
from typing import Dict, Any, List

from .base import MarketProvider

log = logging.getLogger(__name__)


class CoinGeckoProvider(MarketProvider):
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.base_url = "https://api.coingecko.com/api/v3"
        self.headers = {"x-cg-pro-api-key": api_key} if api_key else {}

    def _request(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            log.error(f"CoinGecko error: {e}")
            return {}

    def get_price(self, symbol: str) -> float:
        mapping = {
            "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
            "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
            "SOL": "solana", "DOT": "polkadot", "LINK": "chainlink", "SHIB": "shiba-inu",
            "TRX": "tron",
        }
        coin_id = mapping.get(symbol.upper())
        if not coin_id:
            return 0.0
        data = self._request("/simple/price", params={"ids": coin_id, "vs_currencies": "usd"})
        return float(data.get(coin_id, {}).get("usd", 0))

    def get_market_overview(self) -> Dict[str, Any]:
        data = self._request("/global")
        return {
            "provider": "coingecko",
            "total_market_cap": data.get("data", {}).get("total_market_cap", {}),
            "total_volume": data.get("data", {}).get("total_volume", {})
        }

    def get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        mapping = {
            "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
            "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
            "SOL": "solana", "DOT": "polkadot", "LINK": "chainlink",
            "SHIB": "shiba-inu", "TRX": "tron",
        }
        coin_id = mapping.get(symbol.upper())
        if not coin_id:
            return []
        data = self._request(f"/coins/{coin_id}/market_chart", params={"vs_currency": "usd", "days": days})
        return [{"time": p[0], "price": p[1]} for p in data.get("prices", [])]

    def supports_symbol(self, symbol: str) -> bool:
        supported = ["BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LINK", "SHIB", "TRX"]
        return symbol.upper() in supported
