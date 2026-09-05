import logging
import feedparser
import requests
from typing import List, Dict
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

class NewsFetcher:
    """دریافت اخبار اقتصادی و مالی از منابع معتبر (RSS و API)"""

    def __init__(self):
        # منابع RSS خارجی
        self.rss_feeds = [
            "https://www.reuters.com/markets/rss",
            "https://www.bloomberg.com/feeds/markets.rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://www.cointelegraph.com/rss",
            "https://www.ft.com/?format=rss",
        ]
        # منابع RSS داخلی (طلا، دلار، اقتصاد)
        self.ir_rss = [
            "https://www.tgju.org/rss",
            "https://www.eghtesadonline.com/rss",
        ]
        # API های جایگزین (در صورت نیاز)
        self.api_sources = [
            "https://newsapi.org/v2/everything?q=crypto&apiKey=YOUR_API_KEY",
            "https://min-api.cryptocompare.com/data/v2/news/?lang=EN",
        ]

    def fetch_rss(self, url: str, limit: int = 5) -> List[Dict]:
        """دریافت اخبار از یک RSS feed"""
        try:
            feed = feedparser.parse(url)
            articles = []
            for entry in feed.entries[:limit]:
                articles.append({
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", datetime.now().isoformat()),
                    "source": url,
                })
            return articles
        except Exception as e:
            log.error(f"RSS fetch error from {url}: {e}")
            return []

    def fetch_all(self, limit_per_source: int = 3) -> List[Dict]:
        """دریافت اخبار از تمام منابع"""
        all_news = []
        for feed in self.rss_feeds + self.ir_rss:
            try:
                articles = self.fetch_rss(feed, limit_per_source)
                all_news.extend(articles)
                log.info(f"📰 Fetched {len(articles)} news from {feed}")
            except Exception as e:
                log.warning(f"Failed to fetch {feed}: {e}")
        return all_news

    def fetch_keyword(self, keyword: str, limit: int = 5) -> List[Dict]:
        """دریافت اخبار مرتبط با یک کلمه کلیدی (با API NewsAPI)"""
        try:
            url = f"https://newsapi.org/v2/everything?q={keyword}&language=en&sortBy=relevancy&pageSize={limit}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                articles = []
                for article in data.get("articles", []):
                    articles.append({
                        "title": article.get("title", ""),
                        "summary": article.get("description", ""),
                        "link": article.get("url", ""),
                        "published": article.get("publishedAt", datetime.now().isoformat()),
                        "source": "newsapi",
                    })
                return articles
            return []
        except Exception as e:
            log.error(f"Keyword fetch error: {e}")
            return []

    def get_market_sentiment_news(self) -> List[Dict]:
        """دریافت اخبار مرتبط با بازار (طلا، دلار، رمزارزها)"""
        keywords = ["gold", "dollar", "bitcoin", "crypto", "inflation", "interest rate", "economy"]
        all_news = []
        for keyword in keywords[:3]:  # محدودیت برای جلوگیری از overload
            news = self.fetch_keyword(keyword, limit=2)
            all_news.extend(news)
        return all_news
