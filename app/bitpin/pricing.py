import logging

log = logging.getLogger(__name__)

# نام‌های مختلفی که ممکن است فیلد قیمت در یک تیکر داشته باشد؛ به‌جای فرض
# اینکه همیشه دقیقاً "price" است، همه‌ی گزینه‌های رایج را امتحان می‌کنیم.
_PRICE_FIELDS = ("price", "last_price", "last", "close", "close_price", "price_amount")


def extract_ticker_price(ticker: dict) -> float:
    """قیمت را از یک دیکشنری تیکر، با تلاش روی چند نام فیلد رایج، استخراج می‌کند."""
    if not isinstance(ticker, dict):
        return 0.0
    for field in _PRICE_FIELDS:
        val = ticker.get(field)
        if val is None:
            continue
        try:
            price = float(val)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return 0.0


def find_ticker(tickers, symbol: str) -> dict:
    """یک تیکر مشخص را از پاسخ get_ticker (که ممکن است لیست ساده، پاسخ
    paginated یا یک دیکشنری تکی باشد) پیدا می‌کند."""
    if isinstance(tickers, list):
        for t in tickers:
            if isinstance(t, dict) and t.get("symbol") == symbol:
                return t
        return {}
    if isinstance(tickers, dict):
        results = tickers.get("results")
        if isinstance(results, list):
            for t in results:
                if isinstance(t, dict) and t.get("symbol") == symbol:
                    return t
            return {}
        # ممکن است خود پاسخ، یک تیکر تکی باشد (وقتی API با symbol فیلتر می‌کند)
        if tickers.get("symbol") in (symbol, None):
            return tickers
    return {}


class MarketSymbolResolver:
    """
    نماد دقیق بازار یک دارایی روی بیت‌پین را - به‌جای حدس‌زدن مستقیم
    "{asset}_{quote}" - از لیست واقعی /api/v1/mkt/markets/ پیدا می‌کند.

    چرا لازم است: برای رمزارزهایی با قیمت واحد بسیار پایین (مثل SHIB)
    خیلی از صرافی‌ها نماد را با پیشوند عددی می‌گذارند (مثلاً 1000SHIB)،
    یا کد پایه در لیست بازارها ممکن است دقیقاً با کد دارایی در کیف‌پول
    یکی نباشد. حدس ساده‌ی "{asset}_USDT" روی چنین مواردی شکست می‌خورد و
    قیمت "در دسترس نیست" گزارش می‌شود، حتی وقتی بازار واقعاً وجود دارد.
    """

    def __init__(self, client):
        self.client = client
        self._markets = None
        self._by_base = {}

    def _load(self):
        if self._markets is not None:
            return
        try:
            self._markets = self.client.get_markets() or []
        except Exception as e:
            log.warning(f"خطا در دریافت لیست بازارها برای تشخیص نماد: {e}")
            self._markets = []

        by_base = {}
        # پاسخ get_markets ممکن است لیست ساده یا paginated باشد
        markets = self._markets
        if isinstance(markets, dict):
            markets = markets.get("results") or []

        for m in markets or []:
            if not isinstance(m, dict):
                continue
            symbol = m.get("symbol") or m.get("code") or ""
            base = m.get("base") or m.get("base_currency") or m.get("currency1")
            quote = m.get("quote") or m.get("quote_currency") or m.get("currency2")
            if (not base or not quote) and "_" in symbol:
                base_guess, quote_guess = symbol.split("_", 1)
                base = base or base_guess
                quote = quote or quote_guess
            if not symbol and base and quote:
                symbol = f"{base}_{quote}"
            if not base or not symbol:
                continue
            key = self._normalize(base)
            by_base.setdefault(key, []).append((symbol, (quote or "").upper()))

        self._by_base = by_base

    @staticmethod
    def _normalize(code: str) -> str:
        code = (code or "").strip().upper()
        # حذف پیشوند عددی رایج (مثلاً 1000SHIB -> SHIB) برای تطبیق بهتر
        stripped = code.lstrip("0123456789")
        return stripped or code

    def resolve(self, asset: str, quote: str) -> str:
        """نماد دقیق بازار asset/quote را برمی‌گرداند. اگر تطبیق دقیقی در
        لیست بازارهای واقعی پیدا نشود، لیست گزینه‌های موجود برای آن دارایی
        را لاگ می‌کند (برای عیب‌یابی) و در نهایت به حدس ساده‌ی قبلی
        برمی‌گردد تا رفتار فعلی خراب نشود."""
        self._load()
        key = self._normalize(asset)
        candidates = self._by_base.get(key, [])
        quote_norm = (quote or "").upper()

        for symbol, q in candidates:
            if q == quote_norm:
                return symbol

        fallback = f"{asset}_{quote}"
        if candidates:
            log.info(
                f"⚠️ نماد دقیق {fallback} در بازارهای واقعی پیدا نشد؛ "
                f"گزینه‌های موجود برای دارایی {asset}: {candidates}"
            )
        else:
            log.info(f"⚠️ هیچ بازاری برای دارایی {asset} در لیست بازارهای بیت‌پین پیدا نشد.")
        return fallback
