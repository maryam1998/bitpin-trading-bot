import logging
from typing import Dict, Any, List, Optional

from .bitpin_provider import BitpinProvider
from .brsapi_provider import BrsapiProvider
from .coingecko_provider import CoinGeckoProvider

log = logging.getLogger(__name__)


class MarketDataManager:
    """
    مدیریت یکپارچه داده‌های بازار از چندین منبع
    - بیت‌پین (رمز ارزهای قابل معامله)
    - brsapi (طلا، دلار، سکه، ارزهای فیات)
    - CoinGecko (رمزارزهای خارج از بیت‌پین)
    """

    def __init__(self, settings):
        self.settings = settings
        self.providers = []

        # ۱. Bitpin Provider
        if settings.bitpin_api_key and settings.bitpin_api_secret:
            self.providers.append(BitpinProvider(
                settings.bitpin_api_key,
                settings.bitpin_api_secret,
                settings.bitpin_base_url
            ))
            log.info("✅ Bitpin provider initialized")
        else:
            log.warning("⚠️ Bitpin credentials missing, Bitpin provider disabled")

        # ۲. Brsapi Provider (طلا، دلار، سکه)
        self.providers.append(BrsapiProvider(settings.brsapi_url, settings.brsapi_key))
        log.info("✅ Brsapi provider initialized")

        # ۳. CoinGecko Provider (رمزارزهای خارجی)
        if settings.coingecko_api_key:
            self.providers.append(CoinGeckoProvider(settings.coingecko_api_key))
            log.info("✅ CoinGecko provider initialized (with API key)")
        else:
            self.providers.append(CoinGeckoProvider())  # نسخه رایگان
            log.info("✅ CoinGecko provider initialized (free tier)")

        log.info(f"📊 MarketDataManager ready with {len(self.providers)} providers")

    # ================= قیمت‌ها =================

    def get_all_prices(self, symbols: Optional[List[str]] = None) -> Dict[str, float]:
        """
        دریافت قیمت لحظه‌ای برای لیستی از نمادها
        اگر symbols=None باشد، از watchlist پیش‌فرض استفاده می‌کند
        """
        result = {}
        watchlist = symbols or self.settings.watchlist

        for symbol in watchlist:
            price = self.get_price(symbol)
            if price > 0:
                result[symbol] = price
            else:
                log.debug(f"Price not available for {symbol}")

        log.debug(f"Fetched prices for {len(result)} symbols")
        return result

    def get_price(self, symbol: str) -> float:
        """
        دریافت قیمت یک نماد از اولین provider پشتیبان
        اگر provider اول قیمت را برنگرداند، provider بعدی را امتحان می‌کند
        """
        symbol_upper = symbol.upper()

        for provider in self.providers:
            if provider.supports_symbol(symbol_upper):
                try:
                    price = provider.get_price(symbol_upper)
                    if price > 0:
                        return price
                except Exception as e:
                    log.warning(f"Provider {provider.__class__.__name__} failed for {symbol}: {e}")

        log.debug(f"Price not found for {symbol} from any provider")
        return 0.0

    def get_price_batch(self, symbols: List[str]) -> Dict[str, float]:
        """دریافت قیمت چند نماد به‌صورت دسته‌ای"""
        result = {}
        for symbol in symbols:
            price = self.get_price(symbol)
            if price > 0:
                result[symbol] = price
        return result

    # ================= داده‌های تاریخی =================

    def get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        """
        دریافت داده‌های تاریخی قیمت از اولین provider پشتیبان
        بازگشت: لیستی از دیکشنری‌های {'time': timestamp, 'price': float}
        """
        symbol_upper = symbol.upper()

        for provider in self.providers:
            if provider.supports_symbol(symbol_upper):
                try:
                    data = provider.get_historical(symbol_upper, days)
                    if data and len(data) > 0:
                        log.debug(f"Historical data for {symbol}: {len(data)} points from {provider.__class__.__name__}")
                        return data
                except Exception as e:
                    log.warning(f"Provider {provider.__class__.__name__} failed historical for {symbol}: {e}")

        log.warning(f"No historical data found for {symbol}")
        return []

    def get_historical_batch(self, symbols: List[str], days: int = 30) -> Dict[str, List[Dict]]:
        """دریافت داده‌های تاریخی برای چند نماد"""
        result = {}
        for symbol in symbols:
            data = self.get_historical(symbol, days)
            if data:
                result[symbol] = data
        return result

    # ================= نمای کلی بازار =================

    def get_market_overview(self) -> Dict[str, Any]:
        """
        دریافت نمای کلی بازار از تمام providers
        بازگشت: دیکشنری با کلید provider name و داده‌های مربوطه
        """
        overview = {}

        for provider in self.providers:
            try:
                data = provider.get_market_overview()
                provider_name = data.get("provider", provider.__class__.__name__)
                overview[provider_name] = data
                log.debug(f"Market overview from {provider_name}: {len(data)} fields")
            except Exception as e:
                log.error(f"Market overview error from {provider.__class__.__name__}: {e}")
                overview[provider.__class__.__name__] = {"error": str(e)}

        return overview

    # ================= پرتفولیو =================

    def get_portfolio_value(self, balances: Dict[str, float]) -> float:
        """
        محاسبه ارزش کل پرتفولیو بر اساس قیمت‌های لحظه‌ای
        balances: دیکشنری {asset: amount}
        """
        total = 0.0

        for asset, amount in balances.items():
            if amount <= 0:
                continue

            if asset == "USDT":
                total += amount
            else:
                price = self.get_price(asset)
                if price > 0:
                    total += amount * price
                else:
                    log.warning(f"Could not price asset {asset}, skipping")

        return total

    def get_portfolio_value_with_irt(self, balances: Dict[str, float]) -> Dict[str, float]:
        """
        محاسبه ارزش پرتفولیو به تومان (IRT) و USDT
        بازگشت: {'usdt': float, 'irt': float}
        """
        usdt_price = self.get_price("USDT_IRT")
        if usdt_price <= 0:
            log.warning("USDT/IRT price not available")
            usdt_price = 1.0

        total_usdt = self.get_portfolio_value(balances)
        total_irt = total_usdt * usdt_price

        return {
            "usdt": total_usdt,
            "irt": total_irt,
        }

    # ================= ابزارهای کمکی =================

    def get_provider_for_symbol(self, symbol: str) -> Optional[Any]:
        """پیدا کردن provider که از یک نماد پشتیبانی می‌کند"""
        symbol_upper = symbol.upper()
        for provider in self.providers:
            if provider.supports_symbol(symbol_upper):
                return provider
        return None

    def supports_symbol(self, symbol: str) -> bool:
        """بررسی اینکه آیا حداقل یک provider از نماد پشتیبانی می‌کند"""
        return self.get_provider_for_symbol(symbol) is not None

    def refresh_all(self):
        """به‌روزرسانی تمام providerها (در صورت نیاز)"""
        for provider in self.providers:
            if hasattr(provider, 'refresh'):
                try:
                    provider.refresh()
                    log.debug(f"Refreshed {provider.__class__.__name__}")
                except Exception as e:
                    log.warning(f"Refresh failed for {provider.__class__.__name__}: {e}")
