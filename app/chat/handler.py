import logging
from typing import Dict, Any, List

log = logging.getLogger(__name__)

class ChatHandler:
    """مدیریت پیام‌های دریافتی از تلگرام"""

    def __init__(self, client, discovery, engine, watchlist, max_assets=10, advisor=None, portfolio_mgr=None):
        self.client = client
        self.discovery = discovery
        self.engine = engine
        self.watchlist = watchlist
        self.max_assets = max_assets
        self.advisor = advisor
        self.portfolio_mgr = portfolio_mgr

    def handle(self, chat_id: str, text: str) -> str:
        """پردازش پیام دریافتی و تولید پاسخ"""
        text = text.strip().lower()

        # ===== دستورات ساده =====
        if text in ["/start", "سلام", "hi"]:
            return "👋 سلام! من ربات تریدینگ هوشمند هستم.\nبرای مشاهده راهنما، /help را بفرستید."

        elif text == "/help":
            return (
                "📚 **راهنمای ربات:**\n"
                "/portfolio - نمایش وضعیت کیف پول\n"
                "/signal BTC - دریافت سیگنال برای بیت‌کوین\n"
                "/analysis - تحلیل کلی بازار\n"
                "/forecast BTC - پیش‌بینی قیمت ۳۰ روز آینده\n"
                "/forecast GOLD - پیش‌بینی قیمت طلا\n"
                "/opportunities - نمایش بهترین فرصت‌های امروز\n"
                "سوالات خود را به زبان فارسی بپرسید (مثلاً 'طلا بخرم؟')"
            )

        elif text == "/portfolio" or text == "کیف پول":
            return self._get_portfolio_info()

        elif text.startswith("/signal"):
            parts = text.split()
            if len(parts) > 1:
                return self._get_signal(parts[1].upper())
            return "لطفاً یک ارز را مشخص کنید، مثلاً: /signal BTC"

        elif text.startswith("/forecast"):
            parts = text.split()
            symbol = parts[1].upper() if len(parts) > 1 else "BTC"
            try:
                from app.forecast.report import ForecastReport
                report = ForecastReport().generate_text_report(symbol, days=30)
                return report
            except Exception as e:
                log.error(f"Forecast error for {symbol}: {e}")
                return f"❌ خطا در دریافت پیش‌بینی برای {symbol}: {e}"

        elif text == "/opportunities":
            try:
                from app.forecast.report import ForecastReport
                ops = ForecastReport().get_top_opportunities()
                if not ops:
                    return "🔍 هیچ فرصت واضحی در حال حاضر شناسایی نشد."
                lines = ["💎 **بهترین فرصت‌های امروز:**"]
                for op in ops:
                    lines.append(f"- {op['symbol']}: {op['recommendation']} ({op['change_percent']:+.2f}%)")
                return "\n".join(lines)
            except Exception as e:
                log.error(f"Opportunities error: {e}")
                return f"❌ خطا در دریافت فرصت‌ها: {e}"

        elif "طلا" in text or "دلار" in text:
            return self._get_market_analysis(text)

        else:
            return self._get_ai_response(text)

    # ================= متدهای کمکی =================

    def _get_portfolio_info(self) -> str:
        """دریافت اطلاعات کیف پول از بیت‌پین با نمایش صحیح موجودی و ارزش کل"""
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            if not wallets:
                return "❌ اطلاعات کیف پول در دسترس نیست."

            # دریافت قیمت USDT/IRT برای تبدیل
            try:
                ticker = self.client.get_ticker("USDT_IRT")
                if isinstance(ticker, list):
                    ticker = next((t for t in ticker if t.get("symbol") == "USDT_IRT"), {})
                usdt_irt_price = float(ticker.get("price", 0))
            except:
                usdt_irt_price = 0.0

            # محاسبه مجموع به تومان و USDT
            total_irt = 0.0
            total_usdt = 0.0
            lines = ["📊 **وضعیت کیف پول:**"]

            for item in wallets:
                asset = item.get("asset", "")
                balance = float(item.get("balance", 0))
                available = float(item.get("available", 0))
                frozen = float(item.get("frozen", 0))

                if balance <= 0:
                    continue

                # نمایش هر دارایی
                lines.append(f"• {asset}: {balance:.2f} (قابل استفاده: {available:.2f})")

                # محاسبه ارزش به تومان (برای مجموع)
                if asset == "IRT":
                    value_irt = balance
                elif asset == "USDT":
                    value_irt = balance * usdt_irt_price
                    total_usdt += balance  # جمع USDT با موجودی واقعی
                else:
                    # قیمت ارز به USDT (با استفاده از ticker)
                    try:
                        ticker_asset = self.client.get_ticker(f"{asset}_USDT")
                        if isinstance(ticker_asset, list):
                            ticker_asset = next((t for t in ticker_asset if t.get("symbol") == f"{asset}_USDT"), {})
                        price_usdt = float(ticker_asset.get("price", 0))
                        value_irt = balance * price_usdt * usdt_irt_price
                        total_usdt += balance * price_usdt
                    except:
                        value_irt = 0.0

                total_irt += value_irt

            # اضافه کردن مجموع به گزارش
            lines.append(f"\n💰 مجموع: {total_usdt:.2f} USDT")
            lines.append(f"💰 معادل تومان: {total_irt:,.0f} IRT")

            return "\n".join(lines)

        except Exception as e:
            log.error(f"Portfolio error: {e}")
            return f"❌ خطا در دریافت کیف پول: {e}"

    def _get_signal(self, symbol: str) -> str:
        """دریافت سیگنال برای یک نماد خاص"""
        try:
            ticker = self.client.get_ticker(symbol)
            if not ticker:
                return f"❌ نماد {symbol} یافت نشد."

            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            price = float(ticker.get("price", 0))

            # استفاده از AI Advisor برای تحلیل (اگر در دسترس باشد)
            if self.advisor is not None:
                try:
                    portfolio = {}
                    if self.portfolio_mgr:
                        snapshot = self.portfolio_mgr.fetch_snapshot()
                        portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}
                    opinion = self.advisor.get_recommendation(
                        {"prices": {symbol: price}},
                        portfolio,
                    )
                    return f"📈 **سیگنال {symbol}**\nقیمت فعلی: {price:,.2f} USDT\n\n🤖 تحلیل AI:\n{opinion}"
                except Exception as e:
                    log.warning(f"AI analysis error: {e}")

            # تحلیل ساده (بدون AI)
            if price < 500:
                return f"📈 **سیگنال {symbol}**\nقیمت فعلی: {price:,.2f} USDT\nتوصیه: 🟢 BUY (قیمت پایین است)"
            elif price > 50000:
                return f"📈 **سیگنال {symbol}**\nقیمت فعلی: {price:,.2f} USDT\nتوصیه: 🔴 SELL (قیمت بالا است)"
            else:
                return f"📈 **سیگنال {symbol}**\nقیمت فعلی: {price:,.2f} USDT\nتوصیه: 🟡 HOLD (منتظر بمانید)"

        except Exception as e:
            return f"❌ خطا: {e}"

    def _get_market_analysis(self, text: str) -> str:
        """تحلیل ساده بازار طلا و دلار"""
        try:
            import requests
            resp = requests.get("https://api.brsapi.ir/Market/Gold_Currency.php", timeout=5)
            data = resp.json()
            gold = data.get("price_gold", 0)
            dollar = data.get("price_dollar", 0)
            return (
                f"🏅 **طلا:** {gold:,} تومان\n"
                f"💵 **دلار:** {dollar:,} تومان\n\n"
                "🔍 تحلیل: بازار در حالت عادی قرار دارد."
            )
        except Exception as e:
            return f"❌ خطا در دریافت اطلاعات بازار: {e}"

    def _get_ai_response(self, text: str) -> str:
        """دریافت پاسخ هوشمند از هوش مصنوعی (اگر فعال باشد)"""
        if not self.advisor:
            return "🤖 در حال حاضر هوش مصنوعی در دسترس نیست. لطفاً از دستورات /help استفاده کنید."

        # جمع‌آوری داده‌های واقعی قیمت و پرتفولیو
        prices = {}
        for symbol in self.watchlist[: self.max_assets]:
            try:
                ticker = self.client.get_ticker(symbol)
                if isinstance(ticker, list) and ticker:
                    prices[symbol] = float(ticker[0].get("price", 0))
                elif isinstance(ticker, dict):
                    prices[symbol] = float(ticker.get("price", 0))
            except Exception as e:
                log.warning(f"Could not fetch price for {symbol}: {e}")

        portfolio = {}
        if self.portfolio_mgr:
            try:
                snapshot = self.portfolio_mgr.fetch_snapshot()
                portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}
            except Exception as e:
                log.warning(f"Could not fetch portfolio for AI context: {e}")

        try:
            return self.advisor.get_recommendation({"prices": prices}, portfolio)
        except Exception as e:
            log.error(f"AI response error: {e}")
            return "🤖 خطا در دریافت پاسخ هوش مصنوعی. لطفاً بعداً دوباره تلاش کنید."
