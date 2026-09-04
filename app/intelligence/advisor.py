import logging
import json
from typing import Dict, Any, List, Optional

from openai import OpenAI

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

SYSTEM_PROMPT = """شما یک مشاور مالی و تحلیلگر بازار هستید که به ابزارهای واقعی برای بررسی قیمت لحظه‌ای بازار،
وضعیت پرتفولیو و نمای کلی بازار دسترسی دارید.

قبل از دادن توصیه‌ی نهایی، در صورت نیاز حتماً از ابزارهای در دسترس برای گرفتن اطلاعات به‌روز استفاده کنید
(به‌جای حدس زدن یا تکیه بر داده‌ی قدیمی). می‌توانید در چند مرحله چند ابزار را پشت سر هم صدا بزنید.

توصیه‌ی نهایی را با این ساختار بدهید:
۱. تحلیل کلی بازار
۲. توصیه اصلی (خرید/فروش/نگهداری)
۳. دلیل منطقی (بر اساس داده‌های واقعی که از ابزارها گرفتید)
۴. سطح ریسک (پایین/متوسط/بالا)
۵. قیمت پیشنهادی

توجه مهم: شما فقط اجازه‌ی تحلیل و توصیه دارید. اجرای معامله در اختیار شما نیست؛ تصمیم نهایی اجرا توسط
بخش دیگری از سیستم و با تأیید کاربر گرفته می‌شود."""


class AIAdvisor:
    def __init__(self, settings, market_data_manager=None, portfolio_manager=None, bitpin_client=None):
        self.settings = settings
        self.client = None
        # وابستگی‌های واقعی که ابزارها از طریق آن‌ها اجرا می‌شوند
        self.market_data_manager = market_data_manager
        self.portfolio_manager = portfolio_manager
        self.bitpin_client = bitpin_client

        if settings.ai_enabled and settings.ai_api_key:
            if settings.ai_provider == "openai":
                self.client = OpenAI(api_key=settings.ai_api_key)
                log.info(f"AI enabled: {settings.ai_provider}/{settings.ai_model}")

        self._tool_specs = self._build_tool_specs()
        self._tool_impls = {
            "get_market_prices": self._tool_get_market_prices,
            "get_portfolio_snapshot": self._tool_get_portfolio_snapshot,
            "get_market_overview": self._tool_get_market_overview,
            "get_ticker": self._tool_get_ticker,
        }

    # ================= Public API (بدون تغییر در امضا) =================

    def get_recommendation(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        if not self.client:
            return self._fallback(market_data, portfolio)
        try:
            context = self._prepare_context(market_data, portfolio)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ]
            return self._run_with_tools(messages)
        except Exception as e:
            log.error(f"AI error: {e}")
            return self._fallback(market_data, portfolio)

    # ================= حلقه‌ی Tool Calling =================

    def _run_with_tools(self, messages: List[Dict[str, Any]]) -> str:
        tools_available = any([self.market_data_manager, self.portfolio_manager, self.bitpin_client])

        for iteration in range(MAX_TOOL_ITERATIONS):
            kwargs = dict(
                model=self.settings.ai_model,
                messages=messages,
                temperature=0.4,
                max_tokens=1000,
            )
            if tools_available:
                kwargs["tools"] = self._tool_specs
                kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                return msg.content or ""

            # پیام assistant حاوی درخواست‌های tool call را به تاریخچه اضافه کن
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

            called = []
            for tc in tool_calls:
                result_json = self._execute_tool(tc.function.name, tc.function.arguments)
                called.append(tc.function.name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                })

            log.info(f"AI tool round {iteration + 1}: called {called}")

        log.warning("Max tool iterations reached without a final answer")
        return "⚠️ تحلیل به دلیل نیاز به فراخوانی‌های زیاد ابزار، ناتمام ماند. لطفاً دوباره تلاش کنید."

    def _execute_tool(self, name: str, arguments_json: str) -> str:
        try:
            args = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            args = {}

        impl = self._tool_impls.get(name)
        if not impl:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

        try:
            result = impl(**args)
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            log.warning(f"Tool '{name}' failed: {e}")
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    # ================= تعریف ابزارها (Schema) =================
    # نکته: همه‌ی ابزارهای فعلی فقط خواندنی هستند - هیچ ابزاری قادر به ثبت سفارش نیست.

    def _build_tool_specs(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_market_prices",
                    "description": "دریافت قیمت لحظه‌ای یک یا چند نماد از منابع داده‌ی بازار (بیت‌پین/برساپی/کوین‌گکو).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbols": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "لیست نمادها مثل ['BTC', 'ETH', 'USDT']. اگر ندهید، واچ‌لیست پیش‌فرض استفاده می‌شود.",
                            }
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_portfolio_snapshot",
                    "description": "دریافت وضعیت فعلی کیف پول کاربر شامل موجودی هر دارایی، ارزش کل به تومان/تتر و درصد هر دارایی.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_market_overview",
                    "description": "دریافت نمای کلی بازار از منابع مختلف داده (وضعیت کلی، حجم و غیره).",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_ticker",
                    "description": "دریافت جزئیات دقیق تیکر یک نماد، مستقیماً از صرافی بیت‌پین (مثل BTC_USDT).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string", "description": "نماد بازار، مثل 'BTC_USDT'"}
                        },
                        "required": ["symbol"],
                    },
                },
            },
        ]

    # ================= پیاده‌سازی واقعی ابزارها =================

    def _tool_get_market_prices(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.market_data_manager:
            return {"error": "market_data_manager در دسترس نیست"}
        prices = self.market_data_manager.get_all_prices(symbols)
        return {"prices": prices}

    def _tool_get_portfolio_snapshot(self) -> Dict[str, Any]:
        if not self.portfolio_manager:
            return {"error": "portfolio_manager در دسترس نیست"}
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
            return {"error": "market_data_manager در دسترس نیست"}
        return self.market_data_manager.get_market_overview()

    def _tool_get_ticker(self, symbol: str) -> Dict[str, Any]:
        if not self.bitpin_client:
            return {"error": "bitpin_client در دسترس نیست"}
        ticker = self.bitpin_client.get_ticker(symbol)
        if isinstance(ticker, list):
            ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
        return ticker or {}

    # ================= کمکی: کانتکست اولیه و fallback بدون AI =================

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
