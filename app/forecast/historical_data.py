import logging
import requests
from typing import List, Dict, Optional
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

class HistoricalData:
    """دریافت داده‌های تاریخی قیمت از منابع مختلف"""

    def __init__(self):
        # CoinGecko برای رمزارزها
        self.coingecko_base = "https://api.coingecko.com/api/v3"
        # Cache برای کاهش درخواست‌ها
        self._cache = {}
        # ===== اصلاح: به‌جای درخواست خام تکراری با آدرس/فیلدهای قدیمی و
        # خراب BrsApi، از همون provider مشترک و مقاوم استفاده می‌کنیم =====
        from app.config.settings import settings
        from app.market_data.brsapi_provider import BrsapiProvider
        self._brsapi = BrsapiProvider(settings.brsapi_url, settings.brsapi_key)

    def get_crypto_historical(self, symbol: str, days: int = 30, vs_currency: str = "usd") -> List[Dict]:
        """دریافت داده‌های تاریخی رمزارزها از CoinGecko"""
        mapping = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "BNB": "binancecoin",
            "XRP": "ripple",
            "ADA": "cardano",
            "DOGE": "dogecoin",
            "SOL": "solana",
            "DOT": "polkadot",
            "LINK": "chainlink",
            "SHIB": "shiba-inu",
            "TRX": "tron",
        }

        coin_id = mapping.get(symbol.upper())
        if not coin_id:
            log.warning(f"No CoinGecko mapping for {symbol}")
            return []

        cache_key = f"{symbol}_{days}_{vs_currency}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            url = f"{self.coingecko_base}/coins/{coin_id}/market_chart"
            params = {"vs_currency": vs_currency, "days": days}
            resp = requests.get(url, params=params, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                prices = data.get("prices", [])
                result = [{"time": p[0], "price": p[1]} for p in prices]
                self._cache[cache_key] = result
                log.info(f"📊 Fetched {len(result)} historical prices for {symbol}")
                return result
            else:
                log.warning(f"CoinGecko error for {symbol}: {resp.status_code}")
                return []
        except Exception as e:
            log.error(f"Historical data error for {symbol}: {e}")
            return []

    def get_gold_historical(self, days: int = 30) -> List[Dict]:
        """
        داده‌ی «تاریخی» طلا. brsapi تاریخچه‌ی واقعی رایگان نمی‌دهد، پس فقط
        قیمت لحظه‌ای واقعی را می‌گیریم و حول آن نوسان شبیه‌سازی‌شده می‌سازیم
        (این داده synthetic است و در گزارش نهایی هم صریحاً همین‌طور برچسب
        می‌خورد - نه به عنوان یک پیش‌بینی مطمئن).
        """
        cache_key = f"gold_{days}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            current_price = self._brsapi.get_price("GOLD")
            if not current_price:
                return []

            import random
            result = []
            now = datetime.now()
            for i in range(days, 0, -1):
                price = current_price * (1 + random.uniform(-0.02, 0.02))
                timestamp = (now - timedelta(days=i)).timestamp() * 1000
                result.append({"time": timestamp, "price": price})

            self._cache[cache_key] = result
            log.info(f"📊 Generated {len(result)} historical prices for Gold")
            return result
        except Exception as e:
            log.error(f"Gold historical error: {e}")
            return []

    def get_dollar_historical(self, days: int = 30) -> List[Dict]:
        """داده‌ی «تاریخی» دلار - همان توضیح بالا (synthetic حول قیمت واقعی لحظه‌ای)"""
        cache_key = f"dollar_{days}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            current_price = self._brsapi.get_price("USD")
            if not current_price:
                return []

            import random
            result = []
            now = datetime.now()
            for i in range(days, 0, -1):
                price = current_price * (1 + random.uniform(-0.015, 0.015))
                timestamp = (now - timedelta(days=i)).timestamp() * 1000
                result.append({"time": timestamp, "price": price})

            self._cache[cache_key] = result
            log.info(f"📊 Generated {len(result)} historical prices for Dollar")
            return result
        except Exception as e:
            log.error(f"Dollar historical error: {e}")
            return []

    def get_asset_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        """دریافت داده‌های تاریخی هر دارایی (با تشخیص خودکار نوع)"""
        symbol = symbol.upper()

        # رمزارزها
        crypto_mapping = ["BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LINK", "SHIB", "TRX"]
        if symbol in crypto_mapping:
            return self.get_crypto_historical(symbol, days)

        # طلا
        if symbol in ["GOLD", "XAU"]:
            return self.get_gold_historical(days)

        # دلار
        if symbol in ["USD", "USD_IRT"]:
            return self.get_dollar_historical(days)

        log.warning(f"No historical data available for {symbol}")
        return []
