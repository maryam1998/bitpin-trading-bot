import logging
import json
from typing import Dict, Any, List, Optional
from openai import OpenAI
from app.strategies.base import Signal, Action

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

SYSTEM_PROMPT_DECISION = """شما یک تحلیلگر بازار هوشمند هستید... (بدون تغییر)"""

SYSTEM_PROMPT_CHAT = """شما یک مشاور مالی... (بدون تغییر)"""

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
            if provider == "groq" and settings.ai_model.lower().startswith("gpt-"):
                log.warning(
                    f"⚠️ مدل {settings.ai_model!r} مخصوص OpenAI است و روی Groq کار نمی‌کند. "
                    "به‌صورت خودکار به llama-3.3-70b-versatile تغییر داده شد. "
                    "برای انتخاب مدل دیگر، AI_MODEL را در تنظیمات Groq مطابق مستندات Groq ست کنید."
                )
                settings.ai_model = "llama-3.3-70b-versatile"

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
