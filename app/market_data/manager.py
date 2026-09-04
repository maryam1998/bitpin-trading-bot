import logging
from typing import Dict, Any, List

from .bitpin_provider import BitpinProvider
from .brsapi_provider import BrsapiProvider
from .coingecko_provider import CoinGeckoProvider

log = logging.getLogger(__name__)


class MarketDataManager:
    def __init__(self, settings):
        self.settings = settings
        self.providers = []

        if settings.bitpin_api_key and settings.bitpin_api_secret:
            self.providers.append(BitpinProvider(
                settings.bitpin_api_key,
                settings.bitpin_api_secret,
                settings.bitpin_base_url
            ))
            log.info("Bitpin provider initialized")

        self.providers.append(BrsapiProvider(settings.brsapi_url))
        log.info("Brsapi provider initialized")

        if settings.coingecko_api_key:
            self.providers.append(CoinGeckoProvider(settings.coingecko_api_key))
        else:
            self.providers.append(CoinGeckoProvider())  # free tier
        log.info("CoinGecko provider initialized")

    def get_all_prices(self, symbols: List[str] = None) -> Dict[str, float]:
        result = {}
        watchlist = symbols or self.settings.watchlist
        for symbol in watchlist:
            price = self.get_price(symbol)
            if price > 0:
                result[symbol] = price
        return result

    def get_price(self, symbol: str) -> float:
        for provider in self.providers:
            if provider.supports_symbol(symbol):
                price = provider.get_price(symbol)
                if price > 0:
                    return price
        return 0.0

    def get_market_overview(self) -> Dict[str, Any]:
        overview = {}
        for provider in self.providers:
            try:
                data = provider.get_market_overview()
                overview[data.get("provider")] = data
            except Exception as e:
                log.error(f"Overview error: {e}")
        return overview

    def get_portfolio_value(self, balances: Dict[str, float]) -> float:
        total = 0.0
        for asset, amount in balances.items():
            if asset == "USDT":
                total += amount
            else:
                price = self.get_price(asset)
                total += amount * price
        return total
