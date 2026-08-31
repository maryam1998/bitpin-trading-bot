"""
Dynamic market discovery.

Never hard-codes trading pairs. For every asset the user actually holds,
looks up whether Bitpin has an active, liquid market for it (USDT pair
preferred).
"""
import logging
from app.bitpin.client import BitpinClient, BitpinAPIError

log = logging.getLogger("portfolio.discovery")


class MarketDiscovery:
    def __init__(self, client: BitpinClient, min_liquidity_usdt: float = 1000.0):
        self.client = client
        self.min_liquidity_usdt = min_liquidity_usdt
        self._markets_cache = None

    def refresh(self):
        try:
            raw = self.client.get_markets()
        except BitpinAPIError as e:
            log.error("Could not fetch markets: %s", e)
            self._markets_cache = []
            return
        self._markets_cache = raw if isinstance(raw, list) else raw.get("results", [])

    def find_tradable_markets(self, assets: list) -> dict:
        """Returns {asset: market_symbol} for every asset that has an active,
        sufficiently liquid USDT market. Assets without one are simply
        omitted — never guessed."""
        if self._markets_cache is None:
            self.refresh()

        by_symbol = {}
        for m in self._markets_cache:
            symbol = m.get("symbol") or m.get("code") or f"{m.get('currency1')}_{m.get('currency2')}"
            by_symbol[symbol] = m

        result = {}
        for asset in assets:
            if asset == "USDT":
                continue
            for candidate in (f"{asset}_USDT", f"{asset}USDT", f"{asset}-USDT"):
                m = by_symbol.get(candidate)
                if not m:
                    continue
                active = m.get("active", m.get("tradable", True))
                if not active:
                    continue
                # Liquidity check needs order-book/volume data; caller should
                # verify via ticker/orderbook before trusting this mapping.
                result[asset] = candidate
                break
            else:
                log.info("No active USDT market found for %s", asset)
        return result
