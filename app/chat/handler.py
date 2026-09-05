import logging
from typing import Dict, Any, List
from app.strategies.base import Action

log = logging.getLogger(__name__)

class ChatHandler:
    def __init__(self, client, discovery, engine, watchlist, max_assets=10, advisor=None, portfolio_mgr=None):
        self.client = client
        self.discovery = discovery
        self.engine = engine
        self.watchlist = watchlist
        self.max_assets = max_assets
        self.advisor = advisor
        self.portfolio_mgr = portfolio_mgr

    def handle(self, chat_id: str, text: str) -> str:
        text = text.strip().lower()
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
                "سوالات خود را به زبان فارسی بپرسید."
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
                return ForecastReport().generate_text_report(symbol, days=30)
            except Exception as e:
                return f"❌ خطا در پیش‌بینی: {e}"

        elif text == "/opportunities":
            try:
                from app.forecast.report import ForecastReport
                ops = ForecastReport().get_top_opportunities()
                if not ops:
                    return "🔍 هیچ فرصتی یافت نشد."
                lines = ["💎 **بهترین فرصت‌ها:**"]
                for op in ops:
                    lines.append(f"- {op['symbol']}: {op['recommendation']} ({op['change_percent']:+.2f}%)")
                return "\n".join(lines)
            except Exception as e:
                return f"❌ خطا: {e}"

        elif "طلا" in text or "دلار" in text:
            return self._get_market_analysis(text)

        else:
            return self._get_ai_response(text)

    def _get_portfolio_info(self) -> str:
        """دریافت اطلاعات کیف پول با محاسبه‌ی available = balance - frozen"""
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            if not wallets:
                return "❌ اطلاعات کیف پول در دسترس نیست."

            # دریافت قیمت USDT/IRT
            try:
                ticker = self.client.get_ticker("USDT_IRT")
                if isinstance(ticker, list):
                    ticker = next((t for t in ticker if t.get("symbol") == "USDT_IRT"), {})
                usdt_irt_price = float(ticker.get("price", 0))
            except:
                usdt_irt_price = 0.0

            if usdt_irt_price <= 0:
                return "❌ قیمت USDT/IRT در دسترس نیست."

            # ===== اصلاح: محاسبه available از balance - frozen =====
            balances = {}
            for item in wallets:
                asset = item.get("asset", "")
                balance = float(item.get("balance", 0))
                frozen = float(item.get("frozen", 0))
                # فیلد available واقعاً 0 برمی‌گرداند، پس محاسبه می‌کنیم
                available = float(item.get("available") or (balance - frozen))
                if balance > 0:
                    balances[asset] = {"balance": balance, "available": available}
            # ========================================================

            total_irt = 0.0
            asset_values = {}

            for asset, data in balances.items():
                balance = data["balance"]
                if asset == "IRT":
                    value_irt = balance
                elif asset == "USDT":
                    value_irt = balance * usdt_irt_price
                else:
                    # قیمت ارز به USDT
                    try:
                        ticker_asset = self.client.get_ticker(f"{asset}_USDT")
                        if isinstance(ticker_asset, list):
                            ticker_asset = next((t for t in ticker_asset if t.get("symbol") == f"{asset}_USDT"), {})
                        price_usdt = float(ticker_asset.get("price", 0))
                        if price_usdt > 0:
                            value_irt = balance * price_usdt * usdt_irt_price
                        else:
                            # اگر بازار USDT وجود نداشت، از IRT استفاده کن
                            ticker_irt = self.client.get_ticker(f"{asset}_IRT")
                            if isinstance(ticker_irt, list):
                                ticker_irt = next((t for t in ticker_irt if t.get("symbol") == f"{asset}_IRT"), {})
                            price_irt = float(ticker_irt.get("price", 0))
                            value_irt = balance * price_irt if price_irt > 0 else 0.0
                    except:
                        value_irt = 0.0

                asset_values[asset] = value_irt
                total_irt += value_irt

            total_usdt = total_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0

            lines = ["📊 **وضعیت کیف پول:**"]
            for asset, data in balances.items():
                value_usdt = asset_values.get(asset, 0.0) / usdt_irt_price
                # نمایش موجودی قابل استفاده به‌روز (محاسبه‌شده)
                lines.append(
                    f"• {asset}: {data['balance']:.2f} (قابل استفاده: {data['available']:.2f}) "
                    f"≈ {value_usdt:.2f} USDT"
                )
            lines.append(f"\n💰 مجموع: {total_usdt:.2f} USDT")
            lines.append(f"💰 معادل تومان: {total_irt:,.0f} IRT")

            return "\n".join(lines)

        except Exception as e:
            log.error(f"Portfolio error: {e}")
            return f"❌ خطا در دریافت کیف پول: {e}"

    def _get_signal(self, symbol: str) -> str:
        if not self.advisor:
            return "🤖 AI در دسترس نیست."

        try:
            asset = symbol.split('_')[0]
            signal = self.advisor.decide(asset, symbol)

            if signal.action == Action.WAIT:
                return f"📈 **سیگنال {symbol}**\nقیمت: {signal.current_price:,.2f}\nتوصیه: 🟡 WAIT\nدلیل: {signal.reason}"

            return (
                f"📈 **سیگنال {symbol}**\n"
                f"قیمت: {signal.current_price:,.2f}\n"
                f"توصیه: {'🟢 BUY' if signal.action == Action.BUY else '🔴 SELL'}\n"
                f"دلیل: {signal.reason}\n"
                f"ورود: {signal.entry_price:,.2f}\n"
                f"حد ضرر: {signal.stop_loss:,.2f}\n"
                f"حد سود: {signal.take_profit:,.2f}"
            )
        except Exception as e:
            return f"❌ خطا: {e}"

    def _get_market_analysis(self, text: str) -> str:
        try:
            import requests
            resp = requests.get("https://api.brsapi.ir/Market/Gold_Currency.php", timeout=5)
            data = resp.json()
            gold = data.get("price_gold", 0)
            dollar = data.get("price_dollar", 0)
            return f"🏅 طلا: {gold:,} تومان\n💵 دلار: {dollar:,} تومان"
        except Exception as e:
            return f"❌ خطا: {e}"

    def _get_ai_response(self, text: str) -> str:
        if not self.advisor:
            return "🤖 AI در دسترس نیست."

        prices = {}
        for symbol in self.watchlist[:self.max_assets]:
            try:
                ticker = self.client.get_ticker(symbol)
                if isinstance(ticker, list) and ticker:
                    prices[symbol] = float(ticker[0].get("price", 0))
            except:
                pass

        portfolio = {}
        if self.portfolio_mgr:
            try:
                snapshot = self.portfolio_mgr.fetch_snapshot()
                portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}
            except:
                pass

        try:
            return self.advisor.get_recommendation({"prices": prices}, portfolio)
        except Exception as e:
            return f"❌ خطا: {e}"
