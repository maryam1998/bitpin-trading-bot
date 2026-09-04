import logging
import time
from typing import Dict, Any, List

from ..market_data.manager import MarketDataManager
from .advisor import AIAdvisor

log = logging.getLogger(__name__)


class MarketIntelligence:
    def __init__(self, settings, portfolio_manager=None, bitpin_client=None):
        self.settings = settings
        self.market_manager = MarketDataManager(settings)
        # ===== وصل شد: این AIAdvisor هم حالا به ابزار قیمت/بازار دسترسی دارد =====
        self.advisor = AIAdvisor(
            settings,
            market_data_manager=self.market_manager,
            portfolio_manager=portfolio_manager,
            bitpin_client=bitpin_client,
        )
        # ==========================================================================

    def analyze(self, portfolio: Dict[str, float]) -> Dict[str, Any]:
        log.info("Running market intelligence...")
        prices = self.market_manager.get_all_prices()
        overview = self.market_manager.get_market_overview()
        total_value = self.market_manager.get_portfolio_value(portfolio)
        opportunities = self._find_opportunities(prices, portfolio)

        market_data = {"prices": prices, "overview": overview}
        recommendation = self.advisor.get_recommendation(market_data, portfolio)

        return {
            "timestamp": time.time(),
            "prices": prices,
            "overview": overview,
            "portfolio_value": total_value,
            "opportunities": opportunities,
            "recommendation": recommendation,
            "summary": self._generate_summary(prices, portfolio, total_value)
        }

    def _find_opportunities(self, prices: Dict[str, float], portfolio: Dict[str, float]) -> List[Dict]:
        opportunities = []
        tradable = ["BTC", "ETH", "BNB", "XRP", "ADA", "DOGE"]
        for symbol, price in prices.items():
            if price <= 0:
                continue
            if symbol in tradable and price < 500:
                opportunities.append({"symbol": symbol, "action": "BUY", "price": price, "reason": f"قیمت {price:,.2f} پایین است", "risk": "LOW"})
            if symbol in portfolio and portfolio.get(symbol, 0) > 0 and price > 1000:
                opportunities.append({"symbol": symbol, "action": "SELL", "price": price, "reason": "قیمت بالا رفته", "risk": "LOW"})
        return opportunities

    def _generate_summary(self, prices: Dict[str, float], portfolio: Dict[str, float], total_value: float) -> str:
        summary = f"📊 خلاصه:\n💰 ارزش کل: {total_value:,.2f} USDT\n"
        if prices:
            best = max(prices.items(), key=lambda x: x[1])
            worst = min(prices.items(), key=lambda x: x[1])
            summary += f"🔺 بهترین: {best[0]} {best[1]:,.2f}\n"
            summary += f"🔻 ضعیف‌ترین: {worst[0]} {worst[1]:,.2f}"
        return summary
