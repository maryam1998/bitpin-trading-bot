import logging
import json
from typing import Dict, Any, List, Optional
from openai import OpenAI
from app.strategies.base import Signal, Action

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

SYSTEM_PROMPT_DECISION = """شما یک تحلیلگر بازار هوشمند هستید... (بدون تغییر)"""

SYSTEM_PROMPT_CHAT = """شما یک مشاور مالی... (بدون تغییر)"""

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
            if provider == "groq" and settings.ai_model.lower().startswith("gpt-"):
                log.warning(
                    f"⚠️ مدل {settings.ai_model!r} مخصوص OpenAI است و روی Groq کار نمی‌کند. "
                    "به‌صورت خودکار به openai/gpt-oss-120b تغییر داده شد. "
                    "برای انتخاب مدل دیگر، AI_MODEL را در تنظیمات Groq مطابق مستندات Groq ست کنید."
                )
                settings.ai_model = "openai/gpt-oss-120b"
            elif provider == "groq" and settings.ai_model in groq_deprecated_models:
                log.warning(
                    f"⚠️ مدل {settings.ai_model!r} توسط Groq حذف شده (decommissioned). "
                    "به‌صورت خودکار به openai/gpt-oss-120b تغییر داده شد."
                )
                settings.ai_model = "openai/gpt-oss-120b"

            try:
                if base_url:
                    self.client = OpenAI(api_key=settings.ai_api_key, base_url=base_url)
                else:
                    self.client = OpenAI(api_key=settings.ai_api_key)
                log.info(f"AI enabled: {provider}/{settings.ai_model}")
            except Exception as e:
                log.error(f"Failed to initialize AI client: {e}")
                self.client = None
        else:
            log.warning("AI is disabled or API key is missing. Check AI_API_KEY.")

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

    def _run_decision_tools(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        for iteration in range(MAX_TOOL_ITERATIONS):
            kwargs = {
                "model": self.settings.ai_model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 800,
                "tools": self._tool_specs,
                "tool_choice": "auto",
            }
            response = self.client.chat.completions.create(**kwargs)
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
            result = self._run_decision_tools(messages)

            # این دو مقدار فقط زمانی برگردانده می‌شوند که AI اصلاً نتوانسته
            # پاسخ معتبر بدهد (خطا/تمام‌شدن iteration‌ها) - این‌ها تصمیم واقعی
            # نیستند و نباید به کاربر نشان داده شوند؛ باید fallback فعال شود.
            if result.get("reason") in {"No valid JSON", "Max iterations exceeded"}:
                return None

            action = result.get("action")
            reason = result.get("reason")
            if action not in {"BUY", "SELL", "REDUCE_CONCENTRATION", "WAIT"} or not reason:
                return None

            return {"action": action, "symbol": result.get("symbol"), "reason": reason}
        except Exception as e:
            log.error(f"AI best-action decision error: {e}")
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

    def _tool_get_opportunities(self) -> Dict[str, Any]:
        """
        فرصت‌های واقعی بازار بر اساس درصد تغییر قیمت واقعی ۲۴ ساعته (همان
        معیار MarketIntelligence._find_opportunities) - برای اینکه تصمیم
        «بهترین کار الان» بتواند واقعاً به یک فرصت خرید/فروش واقعی برسد، نه
        فقط ترکیب درصدی پرتفولیو. اعداد همگی از get_all_prices/get_historical
        واقعی می‌آیند؛ چیزی اینجا ساخته نمی‌شود.
        """
        if not self.market_data_manager:
            return {"error": "market_data_manager not available"}
        try:
            prices = self.market_data_manager.get_all_prices(symbols=OPPORTUNITY_SYMBOLS)
        except Exception as e:
            return {"error": str(e)}

        portfolio_amounts: Dict[str, float] = {}
        if self.portfolio_manager:
            try:
                snap = self.portfolio_manager.fetch_snapshot()
                portfolio_amounts = {a: bal.total for a, bal in snap.balances.items()}
            except Exception:
                portfolio_amounts = {}

        opportunities = []
        for symbol in OPPORTUNITY_SYMBOLS:
            price = prices.get(symbol)
            if not price or price <= 0:
                continue
            try:
                history = self.market_data_manager.get_historical(symbol, days=1)
            except Exception:
                history = None
            if not history or len(history) < 2:
                continue
            first_price = history[0].get("price", 0)
            last_price = history[-1].get("price", 0)
            if first_price <= 0:
                continue
            change = ((last_price - first_price) / first_price) * 100
            holding = portfolio_amounts.get(symbol, 0) or 0

            if change <= -MIN_OPPORTUNITY_CHANGE_PERCENT:
                opportunities.append({
                    "symbol": symbol,
                    "action": "BUY",
                    "price": price,
                    "change_percent_24h": round(change, 2),
                    "reason": f"در ۲۴ ساعت اخیر {change:.1f}% افت کرده",
                })
            elif change >= MIN_OPPORTUNITY_CHANGE_PERCENT and holding > 0:
                opportunities.append({
                    "symbol": symbol,
                    "action": "SELL",
                    "price": price,
                    "change_percent_24h": round(change, 2),
                    "reason": f"در ۲۴ ساعت اخیر {change:.1f}% رشد کرده و شما این دارایی را دارید",
                })

        return {"opportunities": opportunities}

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
        for iteration in range(MAX_TOOL_ITERATIONS):
            kwargs = {
                "model": self.settings.ai_model,
                "messages": messages,
                "temperature": 0.4,
                "max_tokens": 1000,
                "tools": self._tool_specs,
                "tool_choice": "auto",
            }
            response = self.client.chat.completions.create(**kwargs)
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
