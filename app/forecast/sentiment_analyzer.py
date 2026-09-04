import logging
import re
from typing import List, Dict, Tuple
from textblob import TextBlob

log = logging.getLogger(__name__)

class SentimentAnalyzer:
    """تحلیل احساسات اخبار و متون اقتصادی"""

    # لیست کلمات کلیدی مثبت و منفی (فارسی و انگلیسی)
    POSITIVE_WORDS = [
        "increase", "growth", "profit", "gain", "rise", "bullish", "positive",
        "افزایش", "رشد", "سود", "صعودی", "مثبت", "بهبود", "رونق"
    ]
    NEGATIVE_WORDS = [
        "decrease", "loss", "crash", "fall", "bearish", "negative", "crisis",
        "کاهش", "ضرر", "سقوط", "نزولی", "منفی", "بحران", "رکود"
    ]

    def __init__(self):
        self._cache = {}

    def analyze(self, text: str) -> Dict[str, float]:
        """
        تحلیل احساسات یک متن
        بازگشت: {'polarity': -1..1, 'subjectivity': 0..1, 'sentiment': 'positive/negative/neutral'}
        """
        if not text:
            return {"polarity": 0.0, "subjectivity": 0.0, "sentiment": "neutral"}

        # استفاده از TextBlob برای تحلیل پایه
        try:
            blob = TextBlob(text)
            polarity = blob.sentiment.polarity  # -1 تا 1
            subjectivity = blob.sentiment.subjectivity  # 0 تا 1
        except:
            polarity = 0.0
            subjectivity = 0.0

        # تقویت تحلیل با کلمات کلیدی
        keyword_score = self._keyword_analysis(text)

        # ترکیب امتیازها
        combined_polarity = (polarity * 0.6) + (keyword_score * 0.4)

        # تعیین احساسات نهایی
        if combined_polarity > 0.1:
            sentiment = "positive"
        elif combined_polarity < -0.1:
            sentiment = "negative"
        else:
            sentiment = "neutral"

        return {
            "polarity": combined_polarity,
            "subjectivity": subjectivity,
            "sentiment": sentiment,
            "keyword_score": keyword_score,
        }

    def _keyword_analysis(self, text: str) -> float:
        """تحلیل بر اساس کلمات کلیدی"""
        text_lower = text.lower()
        positive_count = sum(1 for word in self.POSITIVE_WORDS if word.lower() in text_lower)
        negative_count = sum(1 for word in self.NEGATIVE_WORDS if word.lower() in text_lower)
        total = positive_count + negative_count
        if total == 0:
            return 0.0
        return (positive_count - negative_count) / total

    def analyze_batch(self, texts: List[str]) -> Dict[str, float]:
        """تحلیل احساسات چند متن به‌صورت همزمان"""
        if not texts:
            return {"avg_polarity": 0.0, "avg_subjectivity": 0.0, "overall": "neutral"}

        polarities = []
        subjectivities = []

        for text in texts:
            result = self.analyze(text)
            polarities.append(result["polarity"])
            subjectivities.append(result["subjectivity"])

        avg_polarity = sum(polarities) / len(polarities)
        avg_subjectivity = sum(subjectivities) / len(subjectivities)

        if avg_polarity > 0.1:
            overall = "bullish"
        elif avg_polarity < -0.1:
            overall = "bearish"
        else:
            overall = "neutral"

        return {
            "avg_polarity": avg_polarity,
            "avg_subjectivity": avg_subjectivity,
            "overall": overall,
            "total_samples": len(texts),
        }

    def analyze_news(self, news_list: List[Dict]) -> Dict[str, float]:
        """تحلیل احساسات لیستی از اخبار"""
        if not news_list:
            return {"avg_polarity": 0.0, "overall": "neutral", "total": 0}

        texts = []
        for news in news_list:
            title = news.get("title", "")
            summary = news.get("summary", "")
            full_text = f"{title} {summary}"
            texts.append(full_text)

        return self.analyze_batch(texts)
