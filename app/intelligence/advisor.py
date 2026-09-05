import hashlib
import logging
import json
import re
import time
from typing import Dict, Any, List, Optional
from openai import OpenAI, RateLimitError, APIStatusError
from app.strategies.base import Signal, Action

log = logging.getLogger(__name__)


def _extract_json_object(text: str) -> Optional[str]:
    """
    ===== اصلاح ریشه‌ای: استخراج مقاوم JSON از خروجی مدل =====
    قبلاً فقط اولین "{" و آخرین "}" در کل متن پیدا می‌شد که با هر متن اضافه
    (مثلاً محصور در فنس Markdown ```json ... ``` یا یک "}" اضافه در جای
    دیگر پیام) خراب می‌شد. اینجا:
    ۱) فنس‌های Markdown حذف می‌شوند.
    ۲) از اولین "{" شروع می‌شود و با شمارش دقیق آکولاد باز/بسته (با نادیده
       گرفتن آکولادهای داخل رشته‌ها) اولین شیء JSON کامل و متوازن استخراج
       می‌شود - نه صرفاً فاصله‌ی بین اولین و آخرین آکولاد کل متن.
    برمی‌گرداند: رشته‌ی JSON استخراج‌شده، یا None اگر هیچ شیء متوازنی پیدا نشد.
    """
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1]
    return None


def _normalize_symbol(raw: Optional[str], candidate_symbols) -> Optional[str]:
    """
    ===== اصلاح ریشه‌ای: normalize نام نماد قبل از اعتبارسنجی =====
    مدل ممکن است نماد را به شکل‌های مختلف برگرداند (BTC، BTC/USDT، BTC_USDT،
    btc-usdt، ...). اینجا همه به نماد پایه‌ی بزرگ‌حرف (مثل "BTC") نگاشته
    می‌شود و سپس با نمادهایی که واقعاً بررسی شده‌اند (candidate_symbols)
    مطابقت داده می‌شود. اگر بعد از normalize هم داخل candidates نبود، None
    برگردانده می‌شود (رد می‌شود) - هرگز حدس زده یا ساخته نمی‌شود.
    """
    if not raw or not isinstance(raw, str):
        return None
    base = raw.strip().upper()
    for sep in ("/", "_", "-"):
        if sep in base:
            base = base.split(sep)[0]
            break
    else:
        if base.endswith("USDT") and base != "USDT":
            base = base[:-4]
    return base if base in candidate_symbols else None


# ===== اصلاح ریشه‌ای: JSON Schema برای Structured Output (وقتی SDK/پرووایدر
# پشتیبانی کند). این همان ساختار SYSTEM_PROMPT_BUY_DECISION را به‌صورت schema
# رسمی توصیف می‌کند تا مدل (در پرووایدرهایی که از آن پشتیبانی می‌کنند) اصلاً
# نتواند خروجی خارج از این ساختار تولید کند. اگر پرووایدر پشتیبانی نکند،
# _call_llm خودش یک‌بار بدون این تنظیم دوباره تلاش می‌کند و Parser مقاوم
# (_extract_json_object) همچنان متن آزاد را می‌فهمد. =====
BUY_DECISION_JSON_SCHEMA = {
    "name": "buy_decision",
    "schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["BUY", "WAIT", "SELL"]},
            "symbol": {"type": ["string", "null"]},
            "reason": {"type": "string"},
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        "required": ["decision", "symbol", "reason", "confidence"],
        "additionalProperties": False,
    },
    "strict": True,
}


class _StateCache:
    """
    ===== اصلاح مصرف توکن: cache بر اساس «state» واقعی، نه فقط زمان =====
    برای تصمیم‌های deterministic (decide_best_action / decide_buy_recommendation)
    که ورودی‌شان کاملاً از داده‌ی واقعی پایتون (candidates/opportunities/
    cash_ratio) ساخته می‌شود: اگر همین داده‌ی واقعی نسبت به آخرین بار عوض
    نشده باشد، هیچ دلیلی ندارد که دوباره همان سوال را از LLM بپرسیم و توکن
    مصرف کنیم - نتیجه‌ی قبلی هنوز هم صحیح است. کلید cache از hash خودِ
    context ساخته می‌شود، پس به محض تغییر واقعی بازار/پرتفولیو، cache
    خودکار باطل می‌شود؛ این صرفاً یک cache زمان‌محور کور نیست.
    """

    def __init__(self):
        self._store: Dict[str, Any] = {}

    @staticmethod
    def _hash(context: Any) -> str:
        payload = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def get(self, key: str, context: Any, max_age_seconds: int) -> Optional[Any]:
        entry = self._store.get(key)
        if not entry:
            return None
        state_hash, ts, value = entry
        if state_hash != self._hash(context):
            return None
        if time.time() - ts > max_age_seconds:
            return None
        return value

    def set(self, key: str, context: Any, value: Any) -> None:
        self._store[key] = (self._hash(context), time.time(), value)

# ===== اصلاح ریشه‌ای: این دو پرامپت قبلاً فقط یک متن جایگزین (stub) بودند
# ("... (بدون تغییر)") و عملاً هیچ دستورالعمل واقعی به مدل نمی‌دادند. با یک
# پرامپت تقریباً خالی، مدل هیچ الزامی به استفاده واقعی از ابزارها یا اجتناب
# از پیش‌فرض WAIT نداشت - این یکی از دلایل اصلی رفتار «صبر ثابت» بود.
SYSTEM_PROMPT_DECISION = """شما یک تحلیلگر بازار هوشمند هستید که برای یک نماد مشخص تصمیم معاملاتی می‌گیرید.

قوانین اجباری:
1. تصمیم WAIT نباید پیش‌فرض باشد. پیش از تصمیم‌گیری، با ابزارهای در دسترس (get_technical_indicators،
   get_historical_data، get_market_overview، get_news_headlines) وضعیت واقعی این نماد را بررسی کن.
2. اگر اندیکاتورهای فنی (RSI، EMA، MACD) یا روند قیمت واقعی یک فرصت معتبر BUY یا SELL نشان دادند،
   مجاز و موظفی همان اکشن را برگردانی؛ WAIT فقط زمانی مجاز است که بررسی واقعی هیچ فرصت روشنی نشان ندهد.
3. هرگز عدد، قیمت یا درصدی که از ابزارها یا ورودی کاربر به‌دست نیامده نساز یا حدس نزن.
4. اگر به WAIT رسیدی، در «reason» دلیل مشخص و واقعی از داده‌ی بررسی‌شده بنویس، نه یک جمله‌ی کلی.
5. فقط یک JSON با ساختار زیر برگردان و هیچ متن دیگری قبل یا بعد آن ننویس:
{"action": "BUY" | "SELL" | "WAIT", "reason": "<دلیل به فارسی>", "entry_price": <عدد یا null>,
 "stop_loss": <عدد یا null>, "take_profit": <عدد یا null>}
"""

SYSTEM_PROMPT_CHAT = """شما یک مشاور مالی و معاملاتی هستید که به زبان فارسی به سوالات کاربر درباره‌ی
بازار، پرتفولیوی او، و فرصت‌های معاملاتی پاسخ می‌دهید.

قوانین اجباری:
1. هرگز بر اساس دانش قدیمی یا حدس پاسخ نده. پیش از هر پاسخی درباره‌ی قیمت، فرصت خرید/فروش، یا وضعیت
   بازار، حتماً از ابزارهای در دسترس (get_market_prices، get_portfolio_snapshot، get_market_overview،
   get_opportunities، get_technical_indicators، get_historical_data، get_news_headlines) استفاده کن.
2. اگر کاربر می‌پرسد «چی بخرم» یا مشابه آن، باید چند دارایی واقعی را (نه فقط یکی) با get_opportunities
   یا get_market_prices/get_technical_indicators مقایسه کنی و بهترین گزینه را با دلیل مشخص کنی؛ اگر
   واقعاً هیچ فرصت خرید معتبری نبود، صریح بگو WAIT بهتر است و دلیل واقعی از داده‌ی بررسی‌شده بیاور.
3. هرگز عدد، قیمت، یا درصدی که از خروجی ابزارها یا پیام کاربر به‌دست نیامده نساز.
4. اگر یک ابزار خطا داد یا داده‌ای برنگرداند، این را صریح به کاربر بگو؛ وانمود نکن تحلیل انجام شده.
"""

# ===== اصلاح: تصمیم «بهترین کار الان» دیگر یک Rule ثابت در ChatHandler نیست =====
# قبلاً اگر بیش از نیمی از سرمایه نقد (USDT/IRT) بود، همیشه و بدون قید و شرط
# «🟡 صبر» برگردانده می‌شد، حتی اگه همون لحظه یک فرصت خرید واقعی در بازار
# وجود داشت. این پرامپت به AIAdvisor.decide_best_action داده می‌شود تا واقعاً
# با ابزارهای Portfolio + Market Data + News + Opportunity Analysis بررسی کند
# و فقط در صورتی که واقعاً هیچ فرصت معتبری نبود، به WAIT برسد - آن‌هم با یک
# دلیل واقعی، نه یک جمله‌ی تکراری از پیش نوشته‌شده.
SYSTEM_PROMPT_BEST_ACTION = """شما یک مشاور معاملاتی هستید که باید مشخص کنید «بهترین کار الان» برای کاربر چیست.

قوانین اجباری:
1. تصمیم WAIT/«صبر» نباید پیش‌فرض یا قانون ثابت باشد. پیش از رسیدن به هر تصمیمی، حتماً با ابزارهای
   در دسترس (get_opportunities، get_market_overview، get_technical_indicators، get_news_headlines)
   وضعیت واقعی بازار را بررسی کن - نه فقط درصدهای ترکیب پرتفولیو که در پیام کاربر آمده.
2. اگر از این بررسی یک فرصت خرید واقعی به دست آمد (مثلاً از get_opportunities یا اندیکاتورهای فنی)،
   مجاز و موظفی که اکشن را BUY بگذاری، حتی اگر بخش زیادی از سرمایه نقد (USDT/IRT) باشد.
3. هرگز هیچ عدد، قیمت، درصد یا تاریخ جدیدی که از طریق ورودی یا خروجی همین ابزارها به تو داده نشده
   نساز یا حدس نزن؛ فقط از اعدادی که واقعاً در context یا نتیجه‌ی ابزارها آمده استفاده کن.
4. اگر در نهایت به WAIT رسیدی، در «reason» دلیل مشخص و واقعی بنویس (مثلاً چرا فرصت خرید/فروش معتبری
   در همین بررسی دیده نشد)، نه یک جمله‌ی کلی و همیشگی.
5. فقط و فقط یک JSON با دقیقاً همین ساختار خروجی بده و هیچ متن دیگری قبل یا بعد آن ننویس:
{"action": "BUY" | "SELL" | "REDUCE_CONCENTRATION" | "WAIT", "symbol": "<نماد یا null>", "reason": "<دلیل به فارسی>"}
"""

# نمادهایی که برای پیدا کردن فرصت روزانه بررسی می‌شوند (همان لیست
# MarketIntelligence، برای اینکه ابزار get_opportunities این کلاس هم از
# داده‌ی واقعی و همان معیار استفاده کند - AIAdvisor نمی‌تواند مستقیماً
# MarketIntelligence را import کند چون آن ماژول خودش AIAdvisor را import
# می‌کند و این باعث import چرخه‌ای می‌شود).
OPPORTUNITY_SYMBOLS = ["BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LINK", "SHIB"]
MIN_OPPORTUNITY_CHANGE_PERCENT = 5.0

# ===== پرامپت AI Decision برای فلوی «الان چی بخرم؟» (deterministic، بدون tool-calling) =====
# ===== اصلاح ریشه‌ای: schema خروجی به {decision, symbol, reason, confidence}
# تغییر کرد (به‌جای {action, symbol, reason}) تا با Parser جدید و اعتبارسنجی
# دقیق‌تر symbol/confidence هماهنگ باشد؛ symbol باید به‌صورت "SYMBOL/USDT"
# باشد که در _normalize_symbol به نماد پایه normalize می‌شود. =====
SYSTEM_PROMPT_BUY_DECISION = """شما یک تحلیلگر معاملاتی هستید. به شما یک لیست از چند دارایی واقعی
(candidates) به همراه قیمت لحظه‌ای، درصد تغییر ۲۴ ساعته، وضعیت پرتفولیوی کاربر و عناوین اخبار داده
می‌شود. باید بگویید که آیا الان یک فرصت خرید یا فروش معتبر بین همین دارایی‌ها وجود دارد یا نه.

قوانین اجباری:
1. WAIT پیش‌فرض نیست. اگر یکی از candidates واقعاً افت قیمت قابل‌توجه (is_opportunity=true, action=BUY)
   داشت، باید همان را با decision=BUY انتخاب کنی. اگر یکی از دارایی‌های نگه‌داری‌شده رشد قابل‌توجه
   (is_opportunity=true, action=SELL) داشت، مجازی decision=SELL بدهی. WAIT فقط زمانی مجاز است که
   بررسی واقعی هیچ فرصت روشنی نشان ندهد.
2. باید حداقل چند candidate را با هم مقایسه کنی، نه اینکه فقط اولی را بدون بررسی انتخاب/رد کنی.
3. symbol فقط می‌تواند یکی از نمادهای موجود در candidates باشد، به‌صورت "SYMBOL/USDT" (مثلاً
   "BTC/USDT")؛ هرگز نماد یا عددی که در ورودی نیامده نساز یا حدس نزن. اگر decision=WAIT بود، symbol
   باید null باشد.
4. اگر به WAIT رسیدی، در reason دقیقاً بگو چرا هیچ‌کدام از candidates فرصت معتبری نبودند (با اشاره به
   درصد تغییر واقعی‌شان)، نه یک جمله‌ی کلی و تکراری.
5. confidence باید یک عدد صحیح بین ۰ تا ۱۰۰ باشد که میزان اطمینان واقعی‌ات به همین decision را (بر
   اساس قدرت سیگنال در candidates) نشان می‌دهد؛ عدد دلبخواه یا همیشه‌ثابت نباشد.
6. فقط و فقط یک JSON با دقیقاً این ساختار برگردان - بدون ```json، بدون هیچ متن دیگری قبل یا بعد آن:
{"decision": "BUY" | "WAIT" | "SELL", "symbol": "SYMBOL/USDT یا null", "reason": "<دلیل به فارسی>",
 "confidence": <عدد صحیح بین 0 تا 100>}
"""

PROVIDER_BASE_URLS = {
    "openai": None,  # پیش‌فرض OpenAI (base_url رسمی خودش)
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
}


class AIAdvisor:
    def __init__(self, settings, market_data_manager=None, portfolio_manager=None, bitpin_client=None, repository=None):
        self.settings = settings
        self.client = None
        self.market_data_manager = market_data_manager
        self.portfolio_manager = portfolio_manager
        self.bitpin_client = bitpin_client
        self.repository = repository

        if settings.ai_enabled and settings.ai_api_key:
            provider = (settings.ai_provider or "openai").lower()

            # ===== اصلاح: تشخیص خودکار Groq از روی شکل کلید =====
            # کلیدهای Groq با gsk_ شروع می‌شوند و اگر کاربر AI_PROVIDER را
            # openai (پیش‌فرض) گذاشته باشد ولی کلید Groq بدهد، درخواست به
            # api.openai.com می‌رفت و همیشه با خطای 401 (Incorrect API key)
            # رد می‌شد، چون آن کلید اصلاً برای OpenAI معتبر نیست.
            if provider == "openai" and settings.ai_api_key.startswith("gsk_"):
                log.warning(
                    "⚠️ کلید AI شبیه کلید Groq است (gsk_...) ولی AI_PROVIDER=openai تنظیم شده. "
                    "به‌صورت خودکار روی provider=groq سوییچ می‌شود. برای رفع دائمی این هشدار، "
                    "در تنظیمات AI_PROVIDER=groq را ست کنید."
                )
                provider = "groq"

            base_url = PROVIDER_BASE_URLS.get(provider)
            if provider not in PROVIDER_BASE_URLS:
                log.warning(f"⚠️ AI_PROVIDER ناشناخته: {provider!r}؛ به عنوان سازگار با OpenAI فرض می‌شود.")

            # ===== اصلاح: مدل‌های OpenAI (gpt-...) روی Groq وجود ندارند =====
            # ===== اصلاح ۲: llama-3.3-70b-versatile در Groq از ۱۶ اوت ۲۰۲۶
            # کاملاً حذف شده (decommissioned) و دیگر جواب نمی‌دهد (404) =====
            groq_deprecated_models = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                                       "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct"}

            # ===== اصلاح ریشه‌ای: همین auto-correct باید روی AI_MODEL_FAST هم اجرا شود =====
            # قبلاً فقط settings.ai_model تصحیح می‌شد. اما decide_buy_recommendation
            # (فلوی «چی بخرم؟»)، decide_best_action، و چت آزاد همیشه از self.fast_model
            # (= settings.ai_model_fast یا در نبودش settings.ai_model) استفاده می‌کنند.
            # اگر AI_MODEL_FAST به یک مدل gpt-* یا یک مدل حذف‌شده‌ی Groq ست شده باشد،
            # این مسیرها همیشه با خطای «مدل نامعتبر» (400/404) شکست می‌خوردند - قبل از
            # اینکه اصلاً پاسخی از مدل بگیریم - بدون اینکه هیچ اصلاحی مثل مسیر
            # ai_model روی آن اعمال شود.
            def _autocorrect_model(name: str, label: str) -> str:
                if provider != "groq" or not name:
                    return name
                if name.lower().startswith("gpt-"):
                    log.warning(
                        f"⚠️ {label}={name!r} مخصوص OpenAI است و روی Groq کار نمی‌کند. "
                        "به‌صورت خودکار به openai/gpt-oss-120b تغییر داده شد. "
                        f"برای انتخاب مدل دیگر، {label} را در تنظیمات Groq مطابق مستندات Groq ست کنید."
                    )
                    return "openai/gpt-oss-120b"
                if name in groq_deprecated_models:
                    log.warning(
                        f"⚠️ {label}={name!r} توسط Groq حذف شده (decommissioned). "
                        "به‌صورت خودکار به openai/gpt-oss-120b تغییر داده شد."
                    )
                    return "openai/gpt-oss-120b"
                return name

            settings.ai_model = _autocorrect_model(settings.ai_model, "AI_MODEL")
            settings.ai_model_fast = _autocorrect_model(settings.ai_model_fast, "AI_MODEL_FAST")

            try:
                # ===== اصلاح: max_retries=0 =====
                # کلاینت رسمی OpenAI به‌صورت پیش‌فرض روی خطاهای موقت (از جمله
                # 429) خودش ۲ بار دیگر همان درخواست را (با همان تعداد توکن
                # ورودی) عیناً تکرار می‌کند. وقتی مشکل TPD/rate-limit است، این
                # retry خودکار فقط فشار را روی همان مدل بیشتر می‌کند و مصرف
                # توکن را در بدترین حالت ۳ برابر می‌کند. آن را غیرفعال
                # می‌کنیم و خودمان (در _call_llm) فقط یک‌بار به مدل/پرووایدر
                # پشتیبان (در صورت تنظیم) سوییچ می‌کنیم - نه retry پشت‌سرهم.
                if base_url:
                    self.client = OpenAI(api_key=settings.ai_api_key, base_url=base_url, max_retries=0)
                else:
                    self.client = OpenAI(api_key=settings.ai_api_key, max_retries=0)
                log.info(f"AI enabled: {provider}/{settings.ai_model}")
            except Exception as e:
                log.error(f"Failed to initialize AI client: {e}")
                self.client = None
        else:
            log.warning("AI is disabled or API key is missing. Check AI_API_KEY.")

        # ===== کلاینت پشتیبان (اختیاری) - فقط اگر AI_FALLBACK_* تنظیم شده
        # باشد ساخته می‌شود. فقط زمانی استفاده می‌شود که مدل اصلی 429/TPD
        # بدهد؛ در غیر این صورت هیچ‌وقت صدا زده نمی‌شود. =====
        self.fallback_client = None
        if self.client is not None and settings.ai_fallback_model and settings.ai_fallback_api_key:
            try:
                fb_provider = (settings.ai_fallback_provider or "openai").lower()
                fb_base_url = PROVIDER_BASE_URLS.get(fb_provider)
                self.fallback_client = OpenAI(
                    api_key=settings.ai_fallback_api_key, base_url=fb_base_url, max_retries=0
                )
            except Exception as e:
                log.error(f"Failed to initialize fallback AI client: {e}")
                self.fallback_client = None

        # مدل «سریع/ارزان» برای تصمیم‌های ساده‌ی deterministic. اگر تنظیم
        # نشده باشد، همان ai_model اصلی استفاده می‌شود (رفتار فعلی حفظ می‌شود).
        self.fast_model = settings.ai_model_fast or settings.ai_model
        self.max_tool_iterations = max(1, int(getattr(settings, "ai_max_tool_iterations", 3) or 3))
        self.decision_cache_seconds = int(getattr(settings, "ai_decision_cache_seconds", 180) or 180)
        self._decision_cache = _StateCache()

        self._tool_specs = self._build_tool_specs()
        self._tool_impls = {
            "get_market_prices": self._tool_get_market_prices,
            "get_portfolio_snapshot": self._tool_get_portfolio_snapshot,
            "get_market_overview": self._tool_get_market_overview,
            "get_ticker": self._tool_get_ticker,
            "get_technical_indicators": self._tool_get_technical_indicators,
            "get_historical_data": self._tool_get_historical_data,
            "get_opportunities": self._tool_get_opportunities,
            "get_news_headlines": self._tool_get_news_headlines,
        }

    def _call_llm(self, *, node: str, model: str, messages: List[Dict[str, Any]],
                  max_tokens: int, temperature: float,
                  tools: Optional[List[Dict[str, Any]]] = None,
                  response_format: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """
        ===== نقطه‌ی مرکزی همه‌ی فراخوانی‌های LLM =====
        - Token usage هر درخواست را در لاگ (Debug/Info) ثبت می‌کند.
        - روی 429/TPD کرش نمی‌کند: پیام واضح لاگ می‌کند و فقط یک‌بار (نه
          پشت‌سرهم) به مدل/پرووایدر پشتیبان (در صورت تنظیم) سوییچ می‌کند.
        - اگر هیچ گزینه‌ای جواب ندهد، None برمی‌گرداند تا caller از همان
          fallback قانون‌محور موجودش استفاده کند (رفتار فعلی این بخش‌ها).
        """
        if not self.client:
            return None

        attempts = [(self.client, model, "primary")]
        if self.fallback_client and self.settings.ai_fallback_model:
            attempts.append((self.fallback_client, self.settings.ai_fallback_model, "fallback"))

        kwargs_base = {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            kwargs_base["tools"] = tools
            kwargs_base["tool_choice"] = "auto"

        def _do_call(client, attempt_model, tag, use_response_format: bool):
            call_kwargs = dict(kwargs_base)
            if use_response_format and response_format is not None:
                call_kwargs["response_format"] = response_format
            response = client.chat.completions.create(model=attempt_model, **call_kwargs)
            usage = getattr(response, "usage", None)
            if usage:
                log.info(
                    f"📊 [TOKEN-USAGE] node={node} model={attempt_model} ({tag}) "
                    f"prompt={usage.prompt_tokens} completion={usage.completion_tokens} "
                    f"total={usage.total_tokens}"
                )
            return response

        last_error = None
        for client, attempt_model, tag in attempts:
            try:
                return _do_call(client, attempt_model, tag, use_response_format=True)
            except RateLimitError as e:
                last_error = e
                log.warning(
                    f"⏳ [RATE-LIMIT] node={node} model={attempt_model} ({tag}) به سقف "
                    f"rate limit/TPD رسید: {e}. "
                    + ("در حال امتحان مدل پشتیبان..." if tag == "primary" and self.fallback_client
                       else "مدل/پرووایدر پشتیبانی برای این درخواست تنظیم نشده؛ متوقف می‌شود (بدون retry).")
                )
                continue
            except APIStatusError as e:
                if getattr(e, "status_code", None) == 429:
                    last_error = e
                    log.warning(f"⏳ [RATE-LIMIT] node={node} model={attempt_model} ({tag}) خطای 429: {e}")
                    continue
                # ===== اصلاح ریشه‌ای ۱: اگر response_format (Structured Output/JSON
                # Schema) باعث خطا شده - برخی پرووایدرها (Groq/OpenRouter/Together)
                # از آن پشتیبانی نمی‌کنند - همان کلاینت/مدل را یک‌بار دیگر بدون
                # response_format امتحان کن؛ Parser مقاوم متن (_extract_json_object)
                # همچنان خروجی آزاد را می‌فهمد.
                if response_format is not None:
                    log.warning(
                        f"⚠️ [AI-DECISION] response_format روی {attempt_model} ({tag}) با خطای "
                        f"{type(e).__name__} (status={getattr(e, 'status_code', None)}) رد شد ({e}); "
                        "تلاش مجدد بدون Structured Output."
                    )
                    try:
                        return _do_call(client, attempt_model, tag, use_response_format=False)
                    except Exception as e2:
                        last_error = e2
                        log.error(
                            f"❌ [LLM-ERROR] node={node} model={attempt_model} ({tag}) "
                            f"retry-without-schema type={type(e2).__name__}: {e2}"
                        )
                        return None
                last_error = e
                log.error(f"❌ [LLM-ERROR] node={node} model={attempt_model} ({tag}) type={type(e).__name__}: {e}")
                return None
            except Exception as e:
                # ===== اصلاح ریشه‌ای ۲: قبلاً فقط APIStatusError باعث retry بدون
                # schema می‌شد. اما بعضی پرووایدرها/نسخه‌های SDK وقتی response_format
                # (json_schema) را نمی‌فهمند، خطای دیگری (نه APIStatusError) می‌دهند -
                # مثلاً خطای اعتبارسنجی سمت کلاینت. قبلاً این حالت مستقیم به
                # return None می‌رفت و AI Decision حتی یک‌بار هم بدون Structured
                # Output امتحان نمی‌شد؛ الان همان تلاش دوم اینجا هم انجام می‌شود. =====
                if response_format is not None:
                    log.warning(
                        f"⚠️ [AI-DECISION] response_format روی {attempt_model} ({tag}) با خطای "
                        f"{type(e).__name__} شکست خورد ({e}); تلاش مجدد بدون Structured Output."
                    )
                    try:
                        return _do_call(client, attempt_model, tag, use_response_format=False)
                    except Exception as e2:
                        last_error = e2
                        log.error(
                            f"❌ [LLM-ERROR] node={node} model={attempt_model} ({tag}) "
                            f"retry-without-schema type={type(e2).__name__}: {e2}"
                        )
                        return None
                last_error = e
                log.error(f"❌ [LLM-ERROR] node={node} model={attempt_model} ({tag}) type={type(e).__name__}: {e}")
                return None

        log.error(f"❌ [LLM-EXHAUSTED] node={node} همه‌ی مدل‌های در دسترس rate-limit شدند: {last_error}")
        return None

    def decide(self, asset: str, symbol: str) -> Signal:
        """تصمیم‌گیری با AI یا WAIT در صورت عدم دسترسی"""
        if not self.client:
            log.warning(f"AI not available for {symbol}, returning WAIT")
            return Signal(
                market=symbol,
                action=Action.WAIT,
                reason="AI unavailable",
                current_price=0.0,
            )

        try:
            # دریافت قیمت واقعی از بیت‌پین
            ticker = self.bitpin_client.get_ticker(symbol) if self.bitpin_client else None
            if not ticker:
                return Signal(market=symbol, action=Action.WAIT, reason="No ticker data", current_price=0.0)

            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            current_price = float(ticker.get("price", 0))

            if current_price <= 0:
                return Signal(market=symbol, action=Action.WAIT, reason="Invalid price", current_price=0.0)

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_DECISION},
                {"role": "user", "content": f"لطفاً برای دارایی {asset} با نماد {symbol} و قیمت لحظه‌ای {current_price} یک تصمیم معاملاتی بگیرید."}
            ]

            result_json = self._run_decision_tools(messages)
            return self._parse_decision_to_signal(asset, symbol, result_json, current_price)

        except Exception as e:
            log.error(f"AI decision error for {asset}: {e}")
            return Signal(market=symbol, action=Action.WAIT, reason=f"AI error: {str(e)[:50]}", current_price=0.0)

    def _run_decision_tools(self, messages: List[Dict[str, Any]], node: str = "decide",
                             model: Optional[str] = None) -> Dict[str, Any]:
        model = model or self.settings.ai_model
        for iteration in range(self.max_tool_iterations):
            response = self._call_llm(
                node=f"{node}#{iteration}", model=model,
                messages=messages, temperature=0.3, max_tokens=800, tools=self._tool_specs,
            )
            if response is None:
                return {"action": "WAIT", "reason": "AI rate-limited or unavailable"}
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                content = msg.content or ""
                try:
                    start = content.find('{')
                    end = content.rfind('}') + 1
                    if start != -1 and end > start:
                        return json.loads(content[start:end])
                except:
                    pass
                return {"action": "WAIT", "reason": "No valid JSON"}

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                result_json = self._execute_tool(tc.function.name, tc.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        return {"action": "WAIT", "reason": "Max iterations exceeded"}

    def _parse_decision_to_signal(self, asset: str, symbol: str, decision: Dict[str, Any], current_price: float) -> Signal:
        action_map = {"BUY": Action.BUY, "SELL": Action.SELL, "HOLD": Action.WAIT, "WAIT": Action.WAIT}
        action = action_map.get(decision.get("action", "WAIT"), Action.WAIT)

        if action == Action.WAIT or current_price <= 0:
            return Signal(market=symbol, action=Action.WAIT, reason=decision.get("reason", "WAIT"), current_price=current_price)

        return Signal(
            market=symbol,
            action=action,
            reason=decision.get("reason", "AI decision"),
            current_price=current_price,
            entry_price=decision.get("entry_price", current_price),
            stop_loss=decision.get("stop_loss", 0.0),
            take_profit=decision.get("take_profit", 0.0),
        )

    # Fallback حذف شد! هیچ قانون price < 500 یا > 50000 وجود ندارد.

    def decide_best_action(self, portfolio_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        ===== اصلاح: «بهترین کار الان» دیگر یک Rule ثابت در ChatHandler نیست =====
        به‌جای اینکه صرفاً بر اساس درصد نقد بودن سرمایه (که در portfolio_context
        محاسبه و پاس داده شده) همیشه به WAIT برسیم، اینجا از AI با دسترسی واقعی
        به ابزارهای Portfolio + Market Data + News + Opportunity Analysis
        می‌خواهیم که وضعیت لحظه‌ای بازار را بررسی و تصمیم واقعی بگیرد.

        اعداد/درصدهای ورودی (portfolio_context) و همه‌ی خروجی ابزارها همیشه از
        API/پایتون می‌آیند؛ از AI فقط یک تصمیم (action) و یک دلیل متنی خواسته
        می‌شود، نه ساختن عدد جدید.

        اگر AI در دسترس نباشد یا نتواند خروجی معتبر بدهد، None برمی‌گردد تا
        فراخوان‌کننده (ChatHandler) از منطق fallback قانون‌محور استفاده کند و
        خروجی هیچ‌وقت خالی/خراب نشود.
        """
        if not self.client:
            return None

        # ===== اصلاح مصرف توکن: cache بر اساس state واقعی =====
        # اگر cash_ratio/opportunities/held_assets نسبت به آخرین بار عوض
        # نشده باشد (مثلاً کاربر چند بار پشت‌سرهم «موجودی» را می‌پرسد)، به‌جای
        # صدا زدن دوباره‌ی LLM همان تصمیم قبلی (که هنوز هم صحیح است) برگردانده
        # می‌شود. به محض تغییر واقعی این عددها، cache خودکار باطل می‌شود.
        cached = self._decision_cache.get("best_action", portfolio_context, self.decision_cache_seconds)
        if cached is not None:
            return cached

        try:
            user_content = (
                "این‌ها اعداد واقعی و محاسبه‌شده از API هستند (خودت عدد جدید نساز):\n"
                + json.dumps(portfolio_context, ensure_ascii=False, default=str)
                + "\n\nبا استفاده از ابزارهای در دسترس (بخصوص get_opportunities، get_market_overview و "
                  "در صورت امکان get_news_headlines)، وضعیت واقعی بازار را بررسی کن و طبق قوانین سیستم "
                  "تصمیم بگیر که الان بهترین کار برای کاربر چیست."
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_BEST_ACTION},
                {"role": "user", "content": user_content},
            ]
            # ===== اصلاح مصرف توکن: این تصمیم دیگر «تحلیل پیچیده» نیست
            # (چون cash_ratio/opportunities از قبل در پایتون آماده شده‌اند)،
            # پس از مدل سریع/ارزان استفاده می‌شود، نه gpt-oss-120b. =====
            result = self._run_decision_tools(messages, node="best_action", model=self.fast_model)

            # این دو مقدار فقط زمانی برگردانده می‌شوند که AI اصلاً نتوانسته
            # پاسخ معتبر بدهد (خطا/تمام‌شدن iteration‌ها) - این‌ها تصمیم واقعی
            # نیستند و نباید به کاربر نشان داده شوند؛ باید fallback فعال شود.
            if result.get("reason") in {"No valid JSON", "Max iterations exceeded", "AI rate-limited or unavailable"}:
                return None

            action = result.get("action")
            reason = result.get("reason")
            if action not in {"BUY", "SELL", "REDUCE_CONCENTRATION", "WAIT"} or not reason:
                return None

            final = {"action": action, "symbol": result.get("symbol"), "reason": reason}
            self._decision_cache.set("best_action", portfolio_context, final)
            return final
        except Exception as e:
            log.error(f"AI best-action decision error: {e}")
            return None

    def decide_buy_recommendation(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        ===== AI Decision node برای فلوی «الان چی بخرم؟» =====
        برخلاف decide_best_action (که به مدل اجازه می‌دهد خودش تصمیم بگیرد کدام
        ابزار را صدا بزند - و اگر مدل هیچ ابزاری صدا نزند، تحلیل واقعی انجام
        نمی‌شود)، این متد کاملاً deterministic است: Portfolio/Market/News/
        Opportunity Analysis از قبل در پایتون واقعی اجرا و در «context» جمع
        شده‌اند. از مدل فقط یک تحلیل و تصمیم روی همین داده‌ی واقعی خواسته
        می‌شود - نه فراخوانی ابزار، نه ساختن عدد جدید. این یعنی حتی اگر مدل
        هیچ tool call‌ای نزند (که اینجا اصلاً امکانش نیست چون tools پاس داده
        نمی‌شود)، باز هم همه‌ی داده‌های واقعی جلوی او هست.

        context باید شامل باشد:
          - portfolio: خلاصه‌ی پرتفولیو (cash_ratio_percent، held_assets، ...)
          - candidates: خروجی AIAdvisor.get_market_comparison() (چند دارایی واقعی)
          - news: خروجی AIAdvisor.get_news()

        خروجی در صورت موفقیت: {"action": "BUY"|"WAIT", "symbol": <از میان
        candidates یا None>, "reason": <فارسی>, "considered_symbols": [...]}.
        اگر مدل در دسترس نبود یا JSON نامعتبر/نماد جعلی برگرداند، None
        برمی‌گردد تا caller (ChatHandler) این را صریحاً به کاربر اعلام کند - نه
        اینکه به‌طور خاموش یک WAIT ساختگی نشان بدهد.
        """
        if not self.client:
            return None

        candidate_symbols = {c["symbol"] for c in context.get("candidates", {}).get("candidates", [])}

        # ===== اصلاح مصرف توکن: cache بر اساس state واقعی =====
        # همان منطق decide_best_action: اگر candidates/portfolio/news نسبت
        # به آخرین بار عوض نشده باشند (مثلاً کاربر چند بار «چی بخرم؟» را
        # پشت‌سرهم می‌پرسد)، تصمیم قبلی (که هنوز هم صحیح است) بدون صدا زدن
        # دوباره‌ی LLM برگردانده می‌شود.
        cached = self._decision_cache.get("buy_recommendation", context, self.decision_cache_seconds)
        if cached is not None:
            return cached

        # ===== اصلاح مصرف توکن: context فشرده =====
        # قبلاً کل context (شامل همه‌ی فیلدهای هر candidate و تا ۶ خبر) عیناً
        # به‌عنوان JSON به مدل داده می‌شد. اینجا فقط فیلدهای لازم برای تصمیم
        # نگه داشته می‌شوند و اخبار به ۳ عنوان کوتاه می‌شوند - داده‌ی واقعی
        # حذف نمی‌شود، فقط تکرار/فیلدهای غیرضروری از پرامپت خارج می‌شوند.
        compact_candidates = [
            {k: c[k] for k in ("symbol", "price", "change_percent_24h", "is_opportunity", "action", "reason")
             if k in c}
            for c in context.get("candidates", {}).get("candidates", [])
        ]
        compact_news = [h.get("title", "") for h in context.get("news", {}).get("headlines", [])[:3]]
        compact_context = {
            "portfolio": context.get("portfolio", {}),
            "candidates": compact_candidates,
            "news_headlines": compact_news,
        }

        try:
            user_content = (
                "این‌ها داده‌ی واقعی هستند که همین الان از API/پایتون جمع شده‌اند (خودت عدد جدید نساز):\n"
                + json.dumps(compact_context, ensure_ascii=False, default=str)
                + "\n\nبا مقایسه‌ی چند دارایی موجود در candidates (نه فقط یکی)، تصمیم بگیر که آیا الان "
                  "فرصت خرید معتبری وجود دارد یا نه. طبق قوانین سیستم فقط یک JSON برگردان."
            )
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_BUY_DECISION},
                {"role": "user", "content": user_content},
            ]
            # ===== اصلاح مصرف توکن: تصمیم روی داده‌ی از‌پیش‌آماده‌شده، بدون
            # tool-calling، «تحلیل پیچیده» نیست - مدل سریع/ارزان کافی است. =====
            # ===== اصلاح ریشه‌ای: Structured Output/JSON Schema داده می‌شود تا در
            # پرووایدرهایی که پشتیبانی می‌کنند، مدل اصلاً نتواند خروجی خارج از
            # schema بدهد؛ اگر پرووایدر پشتیبانی نکند، _call_llm خودش بدون آن
            # دوباره تلاش می‌کند و Parser زیر همچنان متن آزاد را می‌فهمد. =====
            response = self._call_llm(
                node="buy_recommendation", model=self.fast_model,
                messages=messages, temperature=0.2, max_tokens=500,
                response_format={"type": "json_schema", "json_schema": BUY_DECISION_JSON_SCHEMA},
            )
            if response is None:
                log.warning(
                    "[AI-DECISION] stage=RAW status=llm_call_failed "
                    "(دلیل دقیق در خط ❌ [LLM-ERROR]/⏳ [RATE-LIMIT] درست بالای همین خط است)"
                )
                log.info("[AI-DECISION] stage=FINAL decision=NO_CONFIDENT_ACTION reason='no LLM response'")
                return None

            # ----- Raw AI -----
            raw_content = response.choices[0].message.content or ""
            log.info(f"[AI-DECISION] stage=RAW content={raw_content[:500]!r}")

            # ----- Parsed -----
            json_text = _extract_json_object(raw_content)
            if not json_text:
                log.warning("[AI-DECISION] stage=PARSED status=failed reason='no JSON object found in output'")
                log.info("[AI-DECISION] stage=FINAL decision=NO_CONFIDENT_ACTION")
                return None
            try:
                result = json.loads(json_text)
            except json.JSONDecodeError as e:
                log.warning(f"[AI-DECISION] stage=PARSED status=failed reason={e}")
                log.info("[AI-DECISION] stage=FINAL decision=NO_CONFIDENT_ACTION")
                return None
            log.info(f"[AI-DECISION] stage=PARSED result={result!r}")

            # ----- Validation -----
            decision = result.get("decision")
            reason = result.get("reason")
            raw_symbol = result.get("symbol")
            confidence_raw = result.get("confidence")

            notes = []
            if decision not in {"BUY", "WAIT", "SELL"}:
                notes.append(f"decision نامعتبر: {decision!r}")
            if not reason or not isinstance(reason, str):
                notes.append("reason خالی/نامعتبر است")

            confidence = None
            try:
                confidence = int(confidence_raw)
                if not (0 <= confidence <= 100):
                    notes.append(f"confidence خارج از بازه‌ی 0-100 است: {confidence_raw!r}")
                    confidence = None
            except (TypeError, ValueError):
                notes.append(f"confidence نامعتبر: {confidence_raw!r}")

            symbol = None
            if not notes and decision in {"BUY", "SELL"}:
                symbol = _normalize_symbol(raw_symbol, candidate_symbols)
                if not symbol:
                    notes.append(
                        f"symbol {raw_symbol!r} پس از normalize در دارایی‌های واقعاً بررسی‌شده "
                        f"{sorted(candidate_symbols)} نیست"
                    )

            valid = not notes
            log.info(f"[AI-DECISION] stage=VALIDATION valid={valid} notes={notes}")

            if not valid:
                log.warning(f"decide_buy_recommendation: rejecting invalid AI output {result!r} ({notes})")
                log.info("[AI-DECISION] stage=FINAL decision=NO_CONFIDENT_ACTION")
                return None

            final = {
                "action": decision,
                "symbol": symbol if decision in {"BUY", "SELL"} else None,
                "reason": reason,
                "confidence": confidence,
                "considered_symbols": sorted(candidate_symbols),
            }
            log.info(f"[AI-DECISION] stage=FINAL decision={final!r}")
            self._decision_cache.set("buy_recommendation", context, final)
            return final
        except Exception as e:
            log.error(f"decide_buy_recommendation error: {e}")
            log.info("[AI-DECISION] stage=FINAL decision=NO_CONFIDENT_ACTION")
            return None

    # ===== ابزارها =====
    def _build_tool_specs(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_market_prices",
                    "description": "دریافت قیمت لحظه‌ای یک یا چند نماد",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbols": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "لیست نمادها مثل ['BTC', 'ETH']",
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_portfolio_snapshot",
                    "description": "دریافت وضعیت فعلی کیف پول کاربر",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_market_overview",
                    "description": "دریافت نمای کلی بازار از منابع مختلف",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_ticker",
                    "description": "دریافت جزئیات تیکر یک نماد از بیت‌پین",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "نماد بازار مثل 'BTC_USDT'"}
                        },
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_technical_indicators",
                    "description": "محاسبه اندیکاتورهای تکنیکال (EMA, RSI, MACD, ATR) برای یک نماد",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "نماد بازار مثل 'BTC_USDT'"}
                        },
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_historical_data",
                    "description": "دریافت داده‌های تاریخی قیمت برای یک نماد",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "نماد بازار مثل 'BTC_USDT'"},
                            "days": {"type": "integer", "description": "تعداد روز", "default": 30}
                        },
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_opportunities",
                    "description": "دریافت فرصت‌های واقعی معاملاتی (خرید/فروش) بر اساس تغییر قیمت واقعی ۲۴ ساعته و دارایی‌های فعلی کاربر",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_news_headlines",
                    "description": "دریافت عناوین اخبار اقتصادی/کریپتو اخیر از منابع RSS معتبر",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "description": "حداکثر تعداد خبر", "default": 6}
                        },
                    },
                },
            },
        ]

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except:
            args = {}

        impl = self._tool_impls.get(name)
        if not impl:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

        try:
            result = impl(**args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    def _tool_get_market_prices(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.market_data_manager:
            return {"error": "market_data_manager not available"}
        return {"prices": self.market_data_manager.get_all_prices(symbols)}

    def _tool_get_portfolio_snapshot(self) -> Dict[str, Any]:
        if not self.portfolio_manager:
            return {"error": "portfolio_manager not available"}
        snap = self.portfolio_manager.fetch_snapshot()
        return {
            "total_value_irt": snap.total_value_irt,
            "total_value_usdt": snap.total_value_usdt,
            "available_usdt": snap.available_usdt,
            "percentages": snap.percentages,
            "balances": {asset: bal.total for asset, bal in snap.balances.items()},
        }

    def _tool_get_market_overview(self) -> Dict[str, Any]:
        if not self.market_data_manager:
            return {"error": "market_data_manager not available"}
        return self.market_data_manager.get_market_overview()

    def _tool_get_ticker(self, symbol: str) -> Dict[str, Any]:
        if not self.bitpin_client:
            return {"error": "bitpin_client not available"}
        ticker = self.bitpin_client.get_ticker(symbol)
        if isinstance(ticker, list):
            ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
        return ticker or {}

    def _tool_get_historical_data(self, symbol: str, days: int = 30) -> Dict[str, Any]:
        """
        داده‌ی تاریخی قیمت یک نماد (برای تحلیل روند/محاسبه اندیکاتور توسط AI).
        این متد قبلاً اصلاً وجود نداشت با اینکه در لیست ابزارها ثبت شده بود -
        همین باعث می‌شد ساخت AIAdvisor همیشه با AttributeError کرش کند و کل
        ربات هیچ‌وقت بالا نیاید.
        """
        if not self.market_data_manager:
            return {"error": "market_data_manager not available"}
        base_symbol = symbol.split("_")[0].upper()
        history = self.market_data_manager.get_historical(base_symbol, days=days)
        if not history:
            return {"error": f"داده‌ی تاریخی برای {symbol} در دسترس نیست", "history": []}
        return {"symbol": symbol, "days": days, "history": history}

    def _tool_get_technical_indicators(self, symbol: str) -> Dict[str, Any]:
        """
        محاسبه‌ی اندیکاتورهای تکنیکال ساده (EMA، RSI، MACD، نوسان/ATR تقریبی)
        از روی داده‌ی تاریخی واقعی - بدون هیچ کتابخانه‌ی خارجی سنگین.
        این متد هم قبلاً وجود نداشت (همان باگ کرش‌کننده‌ی بالا).
        """
        if not self.market_data_manager:
            return {"error": "market_data_manager not available"}

        base_symbol = symbol.split("_")[0].upper()
        history = self.market_data_manager.get_historical(base_symbol, days=30)
        closes = [p.get("price", 0) for p in history if p.get("price", 0) > 0]

        if len(closes) < 15:
            return {"error": f"داده‌ی تاریخی کافی برای محاسبه‌ی اندیکاتور {symbol} در دسترس نیست"}

        import numpy as np
        arr = np.array(closes, dtype=float)

        def ema(values, period):
            values = np.asarray(values, dtype=float)
            alpha = 2 / (period + 1)
            result = np.zeros_like(values)
            result[0] = values[0]
            for i in range(1, len(values)):
                result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
            return result

        def rsi(values, period=14):
            values = np.asarray(values, dtype=float)
            deltas = np.diff(values)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            if len(gains) < period:
                period = len(gains)
            avg_gain = gains[-period:].mean()
            avg_loss = losses[-period:].mean()
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100 - (100 / (1 + rs))

        ema12 = ema(arr, min(12, len(arr) - 1))
        ema26 = ema(arr, min(26, len(arr) - 1))
        macd_line = ema12[-1] - ema26[-1]
        signal_line = ema(ema12 - ema26, 9)[-1]

        # نوسان تقریبی (شبیه ATR ولی فقط از قیمت بسته‌شدن، چون High/Low نداریم)
        daily_returns = np.diff(arr) / arr[:-1]
        volatility_percent = float(np.std(daily_returns) * 100)

        current_rsi = rsi(arr)

        return {
            "symbol": symbol,
            "current_price": float(arr[-1]),
            "ema12": float(ema12[-1]),
            "ema26": float(ema26[-1]),
            "rsi_14": round(float(current_rsi), 2),
            "macd": float(macd_line),
            "macd_signal": float(signal_line),
            "volatility_percent": round(volatility_percent, 2),
            "trend": "صعودی" if ema12[-1] > ema26[-1] else "نزولی",
            "rsi_note": (
                "اشباع خرید (احتمال اصلاح)" if current_rsi > 70
                else "اشباع فروش (احتمال بازگشت)" if current_rsi < 30
                else "خنثی"
            ),
        }

    def get_market_comparison(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        ===== Market Data Tool + Opportunity Analysis (واقعی، بدون AI) =====
        این متد عمومی، مقایسه‌ی واقعی چند دارایی را برمی‌گرداند - نه فقط یک
        دارایی. برای هر نماد کاندید (پیش‌فرض OPPORTUNITY_SYMBOLS)، قیمت لحظه‌ای
        (get_all_prices) و تغییر قیمت ۲۴ ساعته (get_historical) واقعی محاسبه
        می‌شود. این داده مستقیماً به AI Decision داده می‌شود تا AI مجبور شود
        چند دارایی را واقعاً مقایسه کند، نه اینکه فقط یک نماد را حدس بزند.

        خروجی شامل «candidates» (همه‌ی نمادهایی که قیمت واقعی برایشان پیدا شد،
        صرف‌نظر از اینکه فرصت باشند یا نه) و «opportunities» (زیرمجموعه‌ای که
        از آستانه‌ی MIN_OPPORTUNITY_CHANGE_PERCENT عبور کرده‌اند) است.
        اگر market_data_manager در دسترس نباشد یا هیچ قیمت واقعی‌ای برنگردد،
        این صریحاً با "error"/"unpriced_symbols" گزارش می‌شود - چیزی جعل نمی‌شود.
        """
        symbols = symbols or OPPORTUNITY_SYMBOLS
        if not self.market_data_manager:
            return {"error": "market_data_manager not available", "candidates": []}

        try:
            prices = self.market_data_manager.get_all_prices(symbols=symbols)
        except Exception as e:
            return {"error": str(e), "candidates": []}

        portfolio_amounts: Dict[str, float] = {}
        if self.portfolio_manager:
            try:
                snap = self.portfolio_manager.fetch_snapshot()
                portfolio_amounts = {a: bal.total for a, bal in snap.balances.items()}
            except Exception:
                portfolio_amounts = {}

        candidates = []
        unpriced_symbols = []
        for symbol in symbols:
            price = prices.get(symbol)
            if not price or price <= 0:
                unpriced_symbols.append(symbol)
                continue
            try:
                history = self.market_data_manager.get_historical(symbol, days=1)
            except Exception:
                history = None

            change = None
            if history and len(history) >= 2:
                first_price = history[0].get("price", 0)
                last_price = history[-1].get("price", 0)
                if first_price > 0:
                    change = round(((last_price - first_price) / first_price) * 100, 2)

            holding = portfolio_amounts.get(symbol, 0) or 0
            entry = {
                "symbol": symbol,
                "price": price,
                "change_percent_24h": change,
                "held_amount": holding,
            }
            if change is None:
                entry["note"] = "داده‌ی تاریخی ۲۴ ساعته در دسترس نیست"
            elif change <= -MIN_OPPORTUNITY_CHANGE_PERCENT:
                entry["is_opportunity"] = True
                entry["action"] = "BUY"
                entry["reason"] = f"در ۲۴ ساعت اخیر {change:.1f}% افت کرده"
            elif change >= MIN_OPPORTUNITY_CHANGE_PERCENT and holding > 0:
                entry["is_opportunity"] = True
                entry["action"] = "SELL"
                entry["reason"] = f"در ۲۴ ساعت اخیر {change:.1f}% رشد کرده و شما این دارایی را دارید"
            else:
                entry["is_opportunity"] = False
            candidates.append(entry)

        result = {"candidates": candidates}
        if unpriced_symbols:
            result["unpriced_symbols"] = unpriced_symbols
        result["opportunities"] = [c for c in candidates if c.get("is_opportunity")]
        return result

    def get_news(self, limit: int = 6) -> Dict[str, Any]:
        """News Tool عمومی - همان منطق _tool_get_news_headlines اما با نام قابل فراخوانی مستقیم."""
        return self._tool_get_news_headlines(limit=limit)

    def _tool_get_opportunities(self) -> Dict[str, Any]:
        """
        نسخه‌ی سازگار با tool-calling قدیمی: فقط زیرمجموعه‌ی «opportunities»
        را برمی‌گرداند (برای decide()/decide_best_action() که از حلقه‌ی
        function-calling استفاده می‌کنند). منطق واقعی در get_market_comparison
        متمرکز شده تا در دو مسیر جدا تکرار/واگرا نشود.
        """
        comparison = self.get_market_comparison()
        if "error" in comparison:
            return comparison
        return {"opportunities": comparison.get("opportunities", [])}

    def _tool_get_news_headlines(self, limit: int = 6) -> Dict[str, Any]:
        """
        عناوین اخبار اقتصادی/کریپتو واقعی از RSS (NewsFetcher) تا تصمیم
        «بهترین کار الان» صرفاً بر اساس اعداد پرتفولیو نباشد. اگر شبکه/RSS
        در دسترس نبود، خطا برمی‌گردد تا AI بداند نتوانسته اخبار را چک کند
        (نه اینکه فرض کند خبر بدی وجود ندارد).
        """
        try:
            from app.forecast.news_fetcher import NewsFetcher
            articles = NewsFetcher().fetch_all(limit_per_source=2)
            headlines = [
                {"title": a.get("title", ""), "source": a.get("source", "")}
                for a in articles if a.get("title")
            ][:max(1, limit)]
            if not headlines:
                return {"headlines": [], "note": "خبر معتبری در دسترس نبود"}
            return {"headlines": headlines}
        except Exception as e:
            return {"error": str(e)}

    # ===== متدهای چت و یادگیری =====
    def get_recommendation(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        if not self.client:
            return "🔍 AI در دسترس نیست. لطفاً AI_API_KEY را تنظیم کنید."

        try:
            context = self._prepare_context(market_data, portfolio)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_CHAT},
                {"role": "user", "content": context},
            ]
            return self._run_chat_tools(messages)
        except Exception as e:
            log.error(f"AI chat error: {e}")
            return f"❌ خطا: {e}"

    def _run_chat_tools(self, messages: List[Dict[str, Any]]) -> str:
        # ===== اصلاح: این مسیر («BTC چطوره؟» و سوالات آزاد مشابه) دیگر یک
        # تحلیل «پیچیده» نیست - از مدل سریع/ارزان (fast_model) استفاده
        # می‌شود، نه gpt-oss-120b. =====
        for iteration in range(self.max_tool_iterations):
            response = self._call_llm(
                node=f"chat#{iteration}", model=self.fast_model,
                messages=messages, temperature=0.4, max_tokens=700, tools=self._tool_specs,
            )
            if response is None:
                return "🤖 مدل هوش مصنوعی موقتاً به سقف مصرف توکن رسیده (rate limit/TPD)؛ لطفاً کمی بعد دوباره امتحان کن."
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                return msg.content or "پاسخی یافت نشد."

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                result_json = self._execute_tool(tc.function.name, tc.function.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

        return "تحلیل ناتمام ماند."

    def _prepare_context(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        text = "داده‌های بازار:\n"
        for symbol, price in sorted(market_data.get("prices", {}).items()):
            if price > 0:
                text += f"- {symbol}: {price:,.2f}\n"
        if portfolio:
            text += "\nپرتفولیو:\n"
            for asset, amount in portfolio.items():
                if amount > 0:
                    text += f"- {asset}: {amount:,.2f}\n"
        return text

    def learn_from_trade(self, symbol: str, decision: str, entry_price: float, exit_price: float):
        # همان کد قبلی
        pass

    def get_learning_summary(self, symbol: str = None) -> str:
        # همان کد قبلی
        return "📊 داده‌های یادگیری در دسترس نیست"
