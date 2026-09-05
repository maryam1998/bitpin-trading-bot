import logging
from typing import Dict, Any, List
from datetime import datetime

from .news_fetcher import NewsFetcher
from .sentiment_analyzer import SentimentAnalyzer
from .historical_data import HistoricalData
from .predictor import Predictor

log = logging.getLogger(__name__)

class ForecastReport:
    """تولید گزارش نهایی پیش‌بینی با ترکیب اخبار، احساسات، تاریخچه و مدل"""

    def __init__(self):
        self.news_fetcher = NewsFetcher()
        self.sentiment = SentimentAnalyzer()
        self.historical = HistoricalData()
        self.predictor = Predictor()

    def generate_full_report(self, symbol: str, days: int = 30) -> Dict[str, Any]:
        """
        تولید گزارش کامل پیش‌بینی برای یک نماد خاص

        Args:
            symbol: نماد (مثل 'BTC', 'ETH', 'GOLD')
            days: تعداد روز برای پیش‌بینی

        Returns:
            دیکشنری شامل تمام تحلیل‌ها و پیش‌بینی‌ها
        """
        log.info(f"📊 Generating forecast report for {symbol}")

        # ۱. دریافت اخبار مرتبط
        news = self.news_fetcher.fetch_keyword(symbol, limit=5)
        if not news:
            news = self.news_fetcher.get_market_sentiment_news()

        # ۲. تحلیل احساسات اخبار
        sentiment_result = self.sentiment.analyze_news(news)

        # ۳. دریافت داده‌های تاریخی
        historical = self.historical.get_asset_historical(symbol, days=60)

        # ۴. پیش‌بینی
        # طلا و دلار در HistoricalData به‌صورت واقعی تاریخچه ندارند و با نوسان
        # تصادفی حول قیمت فعلی شبیه‌سازی می‌شوند؛ این را به Predictor اطلاع
        # می‌دهیم تا هرگز با اطمینان «بالا» گزارش نشوند.
        is_synthetic = symbol.upper() in ("GOLD", "XAU", "USD", "USD_IRT")
        prediction = self.predictor.generate_prediction_report(symbol, historical, steps=days, is_synthetic=is_synthetic)

        # ۵. تولید گزارش متنی
        summary = self._generate_summary(symbol, sentiment_result, prediction)

        return {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "sentiment": sentiment_result,
            "prediction": prediction,
            "historical_data": historical[-30:],  # آخرین ۳۰ روز
            "news_sample": news[:3],
            "summary": summary,
            "recommendation": prediction.get("recommendation", "HOLD"),
            "confidence": prediction.get("confidence", "متوسط"),
        }

    def _generate_summary(self, symbol: str, sentiment: Dict, prediction: Dict) -> str:
        """تولید خلاصه متنی گزارش"""
        sentiment_text = sentiment.get("overall", "neutral").capitalize()
        pred_price = prediction.get("predicted_price", 0)
        last_price = prediction.get("last_price", 0)
        change = prediction.get("change_percent", 0)
        direction = prediction.get("direction", "نامشخص")
        confidence = prediction.get("confidence", "متوسط")

        summary = f"""
📊 **گزارش پیش‌بینی {symbol}**
━━━━━━━━━━━━━━━━━━━━━━━━━
📰 **تحلیل اخبار**
احساسات کلی بازار: {sentiment_text}
میانگین قطبیت: {sentiment.get('avg_polarity', 0):.2f}
تعداد اخبار تحلیل‌شده: {sentiment.get('total_samples', 0)}

📈 **پیش‌بینی قیمت**
قیمت فعلی: {last_price:,.2f}
پیش‌بینی: {pred_price:,.2f}
تغییرات: {change:+.2f}%
جهت: {direction}
اطمینان: {confidence}

💡 **توصیه نهایی**
**{direction}**

⚠️ **هشدار:** پیش‌بینی‌ها قطعی نیستند و فقط یک تخمین هستند.
همیشه تحقیقات خود را انجام دهید.
{"⚠️ **توجه:** برای این نماد تاریخچه‌ی قیمت واقعی در دسترس نبود و پیش‌بینی بر پایه‌ی داده‌ی تخمینی ساخته شده - این توصیه را جدی نگیرید." if prediction.get("is_synthetic") else ""}
━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        return summary

    def generate_text_report(self, symbol: str, days: int = 30) -> str:
        """تولید گزارش به‌صورت متن ساده (برای ارسال به تلگرام)"""
        report = self.generate_full_report(symbol, days)
        return report.get("summary", "گزارش در دسترس نیست")

    def get_top_opportunities(self, symbols: List[str] = None) -> List[Dict]:
        """پیدا کردن بهترین فرصت‌ها بین چند نماد"""
        if symbols is None:
            symbols = ["BTC", "ETH", "GOLD", "USD", "BNB", "ADA"]

        opportunities = []
        for symbol in symbols:
            try:
                report = self.generate_full_report(symbol, days=14)
                if report.get("recommendation") in ["BUY", "SELL"]:
                    opportunities.append({
                        "symbol": symbol,
                        "recommendation": report["recommendation"],
                        "change_percent": report["prediction"].get("change_percent", 0),
                        "confidence": report.get("confidence", "متوسط"),
                        "summary": report.get("summary", ""),
                    })
            except Exception as e:
                log.error(f"Error analyzing {symbol}: {e}")

        # مرتب‌سازی بر اساس تغییرات
        opportunities.sort(key=lambda x: abs(x["change_percent"]), reverse=True)
        return opportunities[:5]  # ۵ فرصت برتر
