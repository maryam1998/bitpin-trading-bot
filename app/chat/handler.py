import logging
from typing import Dict, Any

log = logging.getLogger(__name__)

class ChatHandler:
    """مدیریت پیام‌های دریافتی از تلگرام"""

    def __init__(self, client, discovery, engine, watchlist, max_assets=10):
        self.client = client
        self.discovery = discovery
        self.engine = engine
        self.watchlist = watchlist
        self.max_assets = max_assets

    def handle(self, chat_id: str, text: str) -> str:
        """پردازش پیام دریافتی و تولید پاسخ"""
        text = text.strip().lower()

        # دستورات ساده
        if text in ["/start", "سلام", "hi"]:
            return "👋 سلام! من ربات تریدینگ هوشمند هستم.\nبرای مشاهده راهنما، /help را بفرستید."

        elif text == "/help":
            return (
                "📚 **راهنمای ربات:**\n"
                "/portfolio - نمایش وضعیت کیف پول\n"
                "/signal BTC - دریافت سیگنال برای بیت‌کوین\n"
                "/analysis - تحلیل کلی بازار\n"
                "سوالات خود را به زبان فارسی بپرسید (مثلاً 'طلا بخرم؟')"
            )

        elif text == "/portfolio" or text == "کیف پول":
            return self._get_portfolio_info()

        elif text.startswith("/signal"):
            parts = text.split()
            if len(parts) > 1:
                return self._get_signal(parts[1].upper())
            return "لطفاً یک ارز را مشخص کنید، مثلاً: /signal BTC"

        elif "طلا" in text or "دلار" in text:
            return self._get_market_analysis(text)

        else:
            return self._get_ai_response(text)

    def _get_portfolio_info(self) -> str:
        """دریافت اطلاعات کیف پول از بیت‌پین"""
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            if not wallets:
                return "❌ اطلاعات کیف پول در دسترس نیست."

            lines = ["📊 **وضعیت کیف پول:**"]
            total_usdt = 0.0

            for item in wallets:
                asset = item.get("asset", "")
                balance = float(item.get("balance", 0))
                available = float(item.get("available", 0))
                if balance > 0:
                    lines.append(f"• {asset}: {balance:.2f} (قابل استفاده: {available:.2f})")
                    if asset == "USDT":
                        total_usdt = available

            lines.append(f"\n💰 مجموع: {total_usdt:.2f} USDT")
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
            price = float(ticker[0].get("price", 0))
            return f"📈 **سیگنال {symbol}**\nقیمت فعلی: {price:,.2f} USDT\nتوصیه: نگهداری (تحلیل دقیق‌تر نیاز است)"
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
        # اگر هوش مصنوعی فعال است، از آن استفاده کن
        if hasattr(self, 'advisor') and self.advisor:
            try:
                return self.advisor.get_recommendation({"prices": {}}, {})
            except:
                pass
        return "🤖 در حال حاضر هوش مصنوعی در دسترس نیست. لطفاً از دستورات /help استفاده کنید."
