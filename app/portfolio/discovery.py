import logging
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

class MarketDiscovery:
    """پیدا کردن بازارهای قابل معامله برای دارایی‌های شما"""

    def __init__(self, client, min_liquidity_usdt: float = 1000.0):
        self.client = client
        self.min_liquidity = min_liquidity_usdt
        self._markets = None

    def refresh(self):
        """دریافت لیست بازارها از بیت‌پین"""
        try:
            self._markets = self.client.get_markets()
            log.info(f"✅ {len(self._markets) if self._markets else 0} بازار پیدا شد")
        except Exception as e:
            log.error(f"خطا در دریافت بازارها: {e}")
            self._markets = []

    def find_tradable_markets(self, held_assets: List[str]) -> Dict[str, str]:
        """پیدا کردن بازارهای قابل معامله برای دارایی‌های موجود"""
        if self._markets is None:
            self.refresh()

        result = {}
        if not self._markets:
            return result

        for asset in held_assets:
            for market in self._markets:
                symbol = market.get("symbol", "")
                if symbol.startswith(asset + "_") and "USDT" in symbol:
                    result[asset] = symbol
                    break

        return result

    def find_market_symbol(self, base_asset: str, quote_asset: str) -> Optional[str]:
        """پیدا کردن سمبل یک جفت‌ارز خاص"""
        if self._markets is None:
            self.refresh()

        target = f"{base_asset}_{quote_asset}"
        for market in self._markets or []:
            if market.get("symbol") == target:
                return target
        return None

    def find_watchlist_markets(self, watchlist: List[str], held_assets: List[str], max_assets: int = 10) -> Dict[str, str]:
        """پیدا کردن بازارها برای واچ‌لیست و دارایی‌های موجود"""
        if self._markets is None:
            self.refresh()

        result = {}
        all_assets = list(set(watchlist + held_assets))

        for asset in all_assets[:max_assets]:
            if asset in ["USDT", "IRT"]:
                continue
            for market in self._markets or []:
                symbol = market.get("symbol", "")
                if symbol.startswith(asset + "_") and ("USDT" in symbol or "IRT" in symbol):
                    result[asset] = symbol
                    break

        return result
