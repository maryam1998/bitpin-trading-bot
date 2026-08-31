"""
Portfolio / Wallet Manager.

Reads real balances from the Bitpin account and auto-discovers whatever
assets are held — never assumes a fixed list like "just BTC".
"""
import logging
from dataclasses import dataclass, field

from app.bitpin.client import BitpinClient, BitpinAPIError
from app.bitpin.models import Balance

log = logging.getLogger("portfolio.manager")


@dataclass
class PortfolioSnapshot:
    balances: dict = field(default_factory=dict)      # asset -> Balance
    prices_usdt: dict = field(default_factory=dict)     # asset -> price in USDT
    total_value_usdt: float = 0.0
    available_usdt: float = 0.0

    def exposure_by_asset(self) -> dict:
        out = {}
        for asset, bal in self.balances.items():
            value = bal.total * self.prices_usdt.get(asset, 0.0)
            out[asset] = value
        return out

    def portfolio_percent(self, asset: str) -> float:
        if self.total_value_usdt <= 0:
            return 0.0
        value = self.exposure_by_asset().get(asset, 0.0)
        return 100.0 * value / self.total_value_usdt


class PortfolioManager:
    def __init__(self, client: BitpinClient):
        self.client = client

    def fetch_snapshot(self) -> PortfolioSnapshot:
        """Reads live wallet balances and current prices. Read-only — never
        places or modifies orders."""
        snap = PortfolioSnapshot()

        try:
            raw_wallets = self.client.get_wallets()
        except BitpinAPIError as e:
            log.error("Could not fetch wallets: %s", e)
            raise

        wallets = raw_wallets if isinstance(raw_wallets, list) else raw_wallets.get("results", raw_wallets)
        for w in wallets:
            asset = w.get("asset") or w.get("currency")
            if not asset:
                continue
            available = float(w.get("balance", w.get("available", 0)) or 0)
            frozen = float(w.get("frozen", w.get("locked", 0)) or 0)
            snap.balances[asset] = Balance(asset=asset, available=available, frozen=frozen)

        try:
            tickers = self.client.get_ticker()
            ticker_list = tickers if isinstance(tickers, list) else tickers.get("results", [])
        except BitpinAPIError as e:
            log.warning("Could not fetch tickers for valuation: %s", e)
            ticker_list = []

        price_by_symbol = {}
        for t in ticker_list:
            sym = t.get("symbol") or t.get("code")
            price = t.get("price") or t.get("last") or t.get("last_price")
            if sym and price is not None:
                price_by_symbol[sym] = float(price)

        for asset in snap.balances:
            if asset == "USDT":
                snap.prices_usdt[asset] = 1.0
                continue
            for candidate in (f"{asset}_USDT", f"{asset}USDT", f"{asset}-USDT"):
                if candidate in price_by_symbol:
                    snap.prices_usdt[asset] = price_by_symbol[candidate]
                    break
            else:
                snap.prices_usdt[asset] = 0.0
                log.info("No USDT price found for %s (asset may not have a USDT pair)", asset)

        snap.total_value_usdt = sum(
            bal.total * snap.prices_usdt.get(asset, 0.0) for asset, bal in snap.balances.items()
        )
        snap.available_usdt = snap.balances.get("USDT", Balance("USDT", 0, 0)).available

        return snap
