import logging
import time
from typing import Dict, Any, List, Optional

from ..market_data.manager import MarketDataManager
from .advisor import AIAdvisor

log = logging.getLogger(__name__)

# نمادهایی که برای پیدا کردن فرصت روزانه بررسی می‌شوند (فقط آنهایی که
# داده‌ی تاریخی واقعی برایشان موجود است - رمزارزها از طریق CoinGecko)
TRADABLE_FOR_OPPORTUNITIES = ["BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LINK", "SHIB"]

# حداقل درصد تغییر قیمت در ۲۴ ساعت اخیر تا یک نماد «فرصت» در نظر گرفته شود.
# قبلاً هر قیمتی زیر ۵۰۰ به عنوان BUY گزارش می‌شد که کاملاً بی‌معنی بود
# (چون تقریباً همه‌ی آلت‌کوین‌ها همیشه زیر ۵۰۰ دلار قیمت دارند).
MIN_OPPORTUNITY_CHANGE_PERCENT = 5.0


class MarketIntelligence:
    def __init__(self, settings, portfolio_manager=None, bitpin_client=None):
        self.settings = settings
        self.market_manager = MarketDataManager(settings)
        self.advisor = AIAdvisor(
            settings,
            market_data_manager=self.market_manager,
            portfolio_manager=portfolio_manager,
            bitpin_client=bitpin_client,
        )

    def analyze(self, portfolio: Dict[str, float], snapshot=None) -> Dict[str, Any]:
        """
        portfolio: {asset: amount} فقط برای سازگاری با نسخه‌های قبلی و AIAdvisor
        snapshot: خروجی PortfolioManager.fetch_snapshot() در صورت وجود؛
                  چون این snapshot از قیمت‌های واقعی بیت‌پین برای دارایی‌های
                  شما استفاده می‌کند، منبع درستِ «ارزش کل کیف پول» همین است
                  (نه محاسبه‌ی موازی و ضعیف‌تر با CoinGecko).
        """
        log.info("Running market intelligence...")
        prices = self.market_manager.get_all_prices()
        overview = self.market_manager.get_market_overview()

        if snapshot is not None and getattr(snapshot, "total_value_usdt", 0) > 0:
            total_value_usdt = snapshot.total_value_usdt
            total_value_irt = snapshot.total_value_irt
        else:
            # fallback (وقتی snapshot در دسترس نیست) - این مسیر ممکن است
            # برای برخی دارایی‌ها که در CoinGecko نیستند قیمت را نداشته باشد
            total_value_usdt = self.market_manager.get_portfolio_value(portfolio)
            total_value_irt = None

        opportunities = self._find_opportunities(prices, portfolio)

        market_data = {"prices": prices, "overview": overview}
        recommendation = self.advisor.get_recommendation(market_data, portfolio)

        return {
            "timestamp": time.time(),
            "prices": prices,
            "overview": overview,
            "portfolio_value": total_value_usdt,
            "opportunities": opportunities,
            "recommendation": recommendation,
            "summary": self._generate_summary(portfolio, total_value_usdt, total_value_irt, opportunities),
        }

    def _get_24h_change_percent(self, symbol: str) -> Optional[float]:
        """
        درصد تغییر قیمت در ۲۴ ساعت گذشته، بر اساس داده‌ی تاریخی واقعی.
        اگر داده‌ای موجود نباشد None برمی‌گرداند (نه صفر، تا با «بدون تغییر»
        اشتباه گرفته نشود).
        """
        try:
            history = self.market_manager.get_historical(symbol, days=1)
            if not history or len(history) < 2:
                return None
            first_price = history[0].get("price", 0)
            last_price = history[-1].get("price", 0)
            if first_price <= 0:
                return None
            return ((last_price - first_price) / first_price) * 100
        except Exception as e:
            log.debug(f"No 24h change available for {symbol}: {e}")
            return None

    def get_opportunities(self, portfolio: Dict[str, float]) -> List[Dict]:
        """
        نسخه‌ی سبک برای گرفتن فرصت‌های واقعی بازار بدون فراخوانی AI advisor -
        برای استفاده در گزارش کیف پول (که فقط به لیست فرصت‌ها نیاز داره، نه
        یک تحلیل متنی کامل که هزینه/تاخیر اضافه ایجاد کنه).
        """
        try:
            prices = self.market_manager.get_all_prices()
            return self._find_opportunities(prices, portfolio)
        except Exception as e:
            log.warning(f"get_opportunities failed: {e}")
            return []

    def _find_opportunities(self, prices: Dict[str, float], portfolio: Dict[str, float]) -> List[Dict]:
        """
        فرصت‌ها بر اساس درصد واقعیِ تغییر قیمت در ۲۴ ساعت اخیر مشخص می‌شوند،
        نه بر اساس اینکه قیمت مطلق «کم» است یا «زیاد» (که برای دارایی‌هایی
        با قیمت واحد پایین مثل DOGE یا SHIB همیشه درست بود و سیگنال بی‌معنی
        تولید می‌کرد).
        """
        opportunities = []
        for symbol in TRADABLE_FOR_OPPORTUNITIES:
            price = prices.get(symbol)
            if not price or price <= 0:
                continue

            change = self._get_24h_change_percent(symbol)
            if change is None:
                continue

            holding = portfolio.get(symbol, 0) or 0

            # افت قابل توجه قیمت روی دارایی که در واچ‌لیست هست: فرصت خرید احتمالی
            if change <= -MIN_OPPORTUNITY_CHANGE_PERCENT:
                opportunities.append({
                    "symbol": symbol,
                    "action": "BUY",
                    "price": price,
                    "change_percent": change,
                    "reason": f"در ۲۴ ساعت اخیر {change:.1f}% افت کرده",
                    "risk": "MEDIUM",
                })
            # رشد قابل توجه روی دارایی که همین الان در کیف پول شما هست: فرصت فروش/سود احتمالی
            elif change >= MIN_OPPORTUNITY_CHANGE_PERCENT and holding > 0:
                opportunities.append({
                    "symbol": symbol,
                    "action": "SELL",
                    "price": price,
                    "change_percent": change,
                    "reason": f"در ۲۴ ساعت اخیر {change:.1f}% رشد کرده و شما این دارایی را دارید",
                    "risk": "MEDIUM",
                })

        return opportunities

    def _generate_summary(
        self,
        portfolio: Dict[str, float],
        total_value_usdt: float,
        total_value_irt: Optional[float],
        opportunities: List[Dict],
    ) -> str:
        summary = f"📊 خلاصه کیف پول:\n💰 ارزش کل: {total_value_usdt:,.2f} USDT"
        if total_value_irt:
            summary += f" (≈ {total_value_irt:,.0f} تومان)"
        summary += "\n"

        held = [a for a, amt in portfolio.items() if amt and amt > 0]
        if held:
            summary += f"📦 دارایی‌های شما: {', '.join(held)}\n"

        if opportunities:
            summary += f"💎 {len(opportunities)} فرصت بر اساس تغییرات ۲۴ ساعته پیدا شد (در پیام بعدی)"
        else:
            summary += "🔍 در حال حاضر تغییر قیمت قابل‌توجهی (بیش از "
            summary += f"{MIN_OPPORTUNITY_CHANGE_PERCENT:.0f}%) در ۲۴ ساعت اخیر دیده نشد."

        return summary
