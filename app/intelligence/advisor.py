import logging
import json
from typing import Dict, Any

from openai import OpenAI

log = logging.getLogger(__name__)


class AIAdvisor:
    def __init__(self, settings):
        self.settings = settings
        self.client = None
        if settings.ai_enabled and settings.ai_api_key:
            if settings.ai_provider == "openai":
                self.client = OpenAI(api_key=settings.ai_api_key)
                log.info(f"AI enabled: {settings.ai_provider}/{settings.ai_model}")

    def get_recommendation(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        if not self.client:
            return self._fallback(market_data, portfolio)
        try:
            context = self._prepare_context(market_data, portfolio)
            response = self.client.chat.completions.create(
                model=self.settings.ai_model,
                messages=[
                    {"role": "system", "content": """شما یک مشاور مالی و تحلیلگر بازار هستید.
بر اساس داده‌های بازار و پرتفولیو، بهترین توصیه را با این ساختار بدهید:
۱. تحلیل کلی بازار
۲. توصیه اصلی (خرید/فروش/نگهداری)
۳. دلیل منطقی
۴. سطح ریسک (پایین/متوسط/بالا)
۵. قیمت پیشنهادی"""},
                    {"role": "user", "content": context}
                ],
                temperature=0.7,
                max_tokens=1000
            )
            return response.choices[0].message.content
        except Exception as e:
            log.error(f"AI error: {e}")
            return self._fallback(market_data, portfolio)

    def _prepare_context(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        text = "داده‌های بازار:\n"
        prices = market_data.get("prices", {})
        for symbol, price in sorted(prices.items()):
            if price > 0:
                text += f"- {symbol}: {price:,.2f}\n"
        if portfolio:
            text += "\nپرتفولیو:\n"
            for asset, amount in portfolio.items():
                if amount > 0:
                    text += f"- {asset}: {amount:,.2f}\n"
        return text

    def _fallback(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        prices = market_data.get("prices", {})
        recs = []
        for symbol, price in prices.items():
            if price == 0:
                continue
            if price < 100 and symbol not in ["USDT", "IRT"]:
                recs.append(f"- {symbol}: {price:,.2f} - پیشنهاد خرید (ارزان)")
            elif price > 10000 and symbol in portfolio:
                recs.append(f"- {symbol}: {price:,.2f} - پیشنهاد فروش (سود خوب)")
        return "\n".join(recs) if recs else "🔍 هیچ فرصت واضحی در حال حاضر شناسایی نشد."
