import logging
import json
from typing import Dict, Any, List, Optional
from openai import OpenAI
from app.strategies.base import Signal, Action

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

SYSTEM_PROMPT_DECISION = """شما یک تحلیلگر بازار هوشمند هستید که باید بر اساس داده‌های لحظه‌ای و وضعیت پرتفولیو، بهترین تصمیم معاملاتی را بگیرید.

شما به ابزارهای زیر دسترسی دارید:
- get_market_prices: دریافت قیمت لحظه‌ای یک یا چند نماد
- get_portfolio_snapshot: دریافت وضعیت کامل کیف پول
- get_market_overview: دریافت نمای کلی بازار (طلا، دلار، ...)
- get_ticker: دریافت تیکر دقیق یک جفت‌ارز
- get_technical_indicators: دریافت اندیکاتورهای تکنیکال (EMA, RSI, MACD, ATR)
- get_historical_data: دریافت داده‌های تاریخی قیمت

وظیفه شما: با استفاده از این ابزارها، داده‌های لازم را جمع‌آوری کنید و سپس یک تصمیم معاملاتی با این ساختار JSON برگردانید:

{
  "action": "BUY" | "SELL" | "HOLD",
  "reason": "دلیل منطقی بر اساس داده‌ها",
  "entry_price": قیمت پیشنهادی برای ورود (عدد),
  "stop_loss": قیمت حد ضرر (عدد),
  "take_profit": قیمت حد سود اول (عدد),
  "take_profit_2": قیمت حد سود دوم (عدد),
  "take_profit_3": قیمت حد سود سوم (عدد),
  "trailing_stop_percent": درصد تریلینگ استاپ (عدد),
  "position_size_percent": درصد از سرمایه آزاد (عدد بین 5 تا 10)
}

قوانین مهم:
- فقط زمانی که حداقل ۳ عامل مثبت (روند صعودی، RSI مناسب، MACD صعودی، حمایت، حجم، مومنتوم) وجود داشته باشد، BUY بدهید.
- اگر بیش از ۲ عامل منفی وجود داشت، SELL یا HOLD بدهید.
- همیشه از ابزارها برای گرفتن داده‌های واقعی استفاده کنید، نه حدس.
- سطح ریسک را در نظر بگیرید و position_size_percent را متناسب با آن تعیین کنید (۵٪ برای ریسک متوسط، ۱۰٪ برای ریسک پایین).
- اگر داده‌های کافی در دسترس نبود، HOLD بدهید.

هیچ‌گاه بدون استفاده از ابزارها تصمیم نگیرید.
"""

SYSTEM_PROMPT_CHAT = """شما یک مشاور مالی و تحلیلگر بازار هستید که به ابزارهای واقعی برای بررسی قیمت لحظه‌ای بازار، وضعیت پرتفولیو و نمای کلی بازار دسترسی دارید.

قبل از دادن توصیه‌ی نهایی، در صورت نیاز حتماً از ابزارهای در دسترس برای گرفتن اطلاعات به‌روز استفاده کنید
(به‌جای حدس زدن یا تکیه بر داده‌ی قدیمی). می‌توانید در چند مرحله چند ابزار را پشت سر هم صدا بزنید.

توصیه‌ی نهایی را با این ساختار بدهید:
۱. تحلیل کلی بازار
۲. توصیه اصلی (خرید/فروش/نگهداری)
۳. دلیل منطقی (بر اساس داده‌های واقعی که از ابزارها گرفتید)
۴. سطح ریسک (پایین/متوسط/بالا)
۵. قیمت پیشنهادی
"""

class AIAdvisor:
    def __init__(self, settings, market_data_manager=None, portfolio_manager=None, bitpin_client=None, repository=None):
        self.settings = settings
        self.client = None
        self.market_data_manager = market_data_manager
        self.portfolio_manager = portfolio_manager
        self.bitpin_client = bitpin_client
        self.repository = repository  # برای ذخیره‌سازی عملکرد AI

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
            "get_technical_indicators": self._tool_get_technical_indicators,
            "get_historical_data": self._tool_get_historical_data,
        }

    # ================= متد عمومی برای تصمیم‌گیری =================

    def decide(self, asset: str, symbol: str) -> Signal:
        """گرفتن تصمیم معاملاتی برای یک دارایی خاص با استفاده از AI و Tool Calling"""
        if not self.client:
            log.warning("AI not available, using fallback strategy")
            return self._fallback_decision(asset, symbol)

        try:
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_DECISION},
                {"role": "user", "content": f"لطفاً برای دارایی {asset} با نماد {symbol} یک تصمیم معاملاتی بگیرید. از ابزارهای موجود برای دریافت داده‌های لازم استفاده کنید."}
            ]

            result_json = self._run_decision_tools(messages)
            return self._parse_decision_to_signal(asset, symbol, result_json)

        except Exception as e:
            log.error(f"AI decision error for {asset}: {e}")
            return self._fallback_decision(asset, symbol)

    def _run_decision_tools(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """حلقه‌ی Tool Calling برای تصمیم‌گیری با درخواست JSON نهایی"""
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
                    else:
                        log.warning("No JSON found in final response")
                        return {"action": "HOLD", "reason": "No valid decision JSON"}
                except json.JSONDecodeError:
                    log.warning("Failed to parse decision JSON")
                    return {"action": "HOLD", "reason": "Invalid JSON format"}

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

        log.warning("Max iterations reached without final decision")
        return {"action": "HOLD", "reason": "Max iterations exceeded"}

    def _parse_decision_to_signal(self, asset: str, symbol: str, decision: Dict[str, Any]) -> Signal:
        action_map = {
            "BUY": Action.BUY,
            "SELL": Action.SELL,
            "HOLD": Action.WAIT,
        }
        action = action_map.get(decision.get("action", "HOLD"), Action.WAIT)
        current_price = decision.get("entry_price", 0.0)

        return Signal(
            market=symbol,
            action=action,
            reason=decision.get("reason", "AI decision"),
            current_price=current_price,
            entry_price=decision.get("entry_price", current_price),
            stop_loss=decision.get("stop_loss", 0.0),
            take_profit=decision.get("take_profit", 0.0),
            net_edge_percent=0.0,
            slippage_percent=0.5,
        )

    def _fallback_decision(self, asset: str, symbol: str) -> Signal:
        """تصمیم ساده در صورت عدم دسترسی به AI"""
        try:
            ticker = self.bitpin_client.get_ticker(symbol)
            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            price = float(ticker.get("price", 0))
        except:
            price = 0

        if price <= 0:
            return Signal(market=symbol, action=Action.WAIT, reason="Price unavailable", current_price=0)

        if price < 500:
            return Signal(
                market=symbol,
                action=Action.BUY,
                reason=f"AI unavailable, fallback: price {price:.2f} below 500",
                current_price=price,
                entry_price=price * 1.01,
            )
        elif price > 50000:
            return Signal(
                market=symbol,
                action=Action.SELL,
                reason=f"AI unavailable, fallback: price {price:.2f} above 50000",
                current_price=price,
                entry_price=price * 0.99,
            )
        else:
            return Signal(market=symbol, action=Action.WAIT, reason="AI unavailable, no condition met", current_price=price)

    # ================= ابزارها =================

    def _tool_get_technical_indicators(self, symbol: str) -> Dict[str, Any]:
        """محاسبه اندیکاتورهای تکنیکال برای یک نماد"""
        if not self.bitpin_client:
            return {"error": "bitpin_client not available"}

        try:
            import requests
            import numpy as np

            mapping = {
                "BTC_USDT": "bitcoin",
                "ETH_USDT": "ethereum",
                "BNB_USDT": "binancecoin",
                "XRP_USDT": "ripple",
                "ADA_USDT": "cardano",
                "DOGE_USDT": "dogecoin",
                "SOL_USDT": "solana",
                "DOT_USDT": "polkadot",
                "LINK_USDT": "chainlink",
                "SHIB_USDT": "shiba-inu",
            }
            coin_id = mapping.get(symbol)
            if not coin_id:
                return {"error": f"No mapping for {symbol}"}

            url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
            params = {"vs_currency": "usd", "days": 30}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return {"error": f"CoinGecko error: {resp.status_code}"}

            data = resp.json()
            prices = [p[1] for p in data.get("prices", [])]
            if len(prices) < 50:
                return {"error": "Not enough data"}

            prices_np = np.array(prices[-200:])
            ema20 = np.mean(prices_np[-20:]) if len(prices_np) >= 20 else 0
            ema50 = np.mean(prices_np[-50:]) if len(prices_np) >= 50 else 0
            ema200 = np.mean(prices_np) if len(prices_np) >= 200 else 0

            delta = np.diff(prices_np)
            gain = (delta[delta > 0]).sum() / 14
            loss = (-delta[delta < 0]).sum() / 14
            rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 100

            ema12 = np.mean(prices_np[-12:]) if len(prices_np) >= 12 else 0
            ema26 = np.mean(prices_np[-26:]) if len(prices_np) >= 26 else 0
            macd = ema12 - ema26

            high = prices_np.max()
            low = prices_np.min()
            atr = (high - low) / 14

            return {
                "symbol": symbol,
                "current_price": prices_np[-1],
                "ema20": ema20,
                "ema50": ema50,
                "ema200": ema200,
                "rsi": rsi,
                "macd": macd,
                "atr": atr,
                "trend": "up" if ema20 > ema50 > ema200 else "down",
                "overbought": rsi > 70,
                "oversold": rsi < 30,
            }
        except Exception as e:
            log.error(f"Technical indicators error: {e}")
            return {"error": str(e)}

    def _tool_get_historical_data(self, symbol: str, days: int = 30) -> Dict[str, Any]:
        """دریافت داده‌های تاریخی قیمت"""
        if not self.bitpin_client:
            return {"error": "bitpin_client not available"}

        try:
            mapping = {
                "BTC_USDT": "bitcoin",
                "ETH_USDT": "ethereum",
                "BNB_USDT": "binancecoin",
                "XRP_USDT": "ripple",
                "ADA_USDT": "cardano",
                "DOGE_USDT": "dogecoin",
            }
            coin_id = mapping.get(symbol)
            if not coin_id:
                return {"error": "No mapping for symbol"}

            import requests
            url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
            params = {"vs_currency": "usd", "days": days}
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return {"prices": data.get("prices", [])}
            else:
                return {"error": f"CoinGecko error: {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    # ================= ابزارهای پایه =================

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

    def _tool_get_market_prices(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        if not self.market_data_manager:
            return {"error": "market_data_manager not available"}
        prices = self.market_data_manager.get_all_prices(symbols)
        return {"prices": prices}

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

    # ================= متدهای چت (کامل) =================

    def get_recommendation(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        """تحلیل مکمل برای چت با استفاده از Tool Calling"""
        if not self.client:
            return self._fallback_chat(market_data, portfolio)

        try:
            context = self._prepare_context(market_data, portfolio)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT_CHAT},
                {"role": "user", "content": context},
            ]
            return self._run_chat_tools(messages)
        except Exception as e:
            log.error(f"AI chat error: {e}")
            return self._fallback_chat(market_data, portfolio)

    def _run_chat_tools(self, messages: List[Dict[str, Any]]) -> str:
        """حلقه‌ی Tool Calling برای چت (بدون درخواست JSON خاص)"""
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
                return msg.content or "نمی‌توانم پاسخی تولید کنم."

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

        log.warning("Max chat iterations reached")
        return "تحلیل ناتمام ماند. لطفاً دوباره تلاش کنید."

    def _prepare_context(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        """ساخت پرامپت اولیه برای چت"""
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
        else:
            text += "\nپرتفولیو: در دسترس نیست"

        text += "\n\nلطفاً بر اساس داده‌های بالا تحلیل خود را ارائه دهید. در صورت نیاز از ابزارها استفاده کنید."
        return text

    def _fallback_chat(self, market_data: Dict[str, Any], portfolio: Dict[str, float]) -> str:
        """پاسخ ساده در صورت عدم دسترسی به AI"""
        prices = market_data.get("prices", {})
        recs = []
        for symbol, price in prices.items():
            if price == 0:
                continue
            if price < 100 and symbol not in ["USDT", "IRT"]:
                recs.append(f"- {symbol}: {price:,.2f} - پیشنهاد خرید (ارزان)")
            elif price > 10000 and symbol in portfolio:
                recs.append(f"- {symbol}: {price:,.2f} - پیشنهاد فروش (سود خوب)")

        if recs:
            return "🔍 تحلیل ساده:\n" + "\n".join(recs)
        else:
            return "🔍 هیچ فرصت واضحی در حال حاضر شناسایی نشد."

    # ================= بخش جدید: یادگیری از نتایج =================

    def learn_from_trade(self, symbol: str, decision: str, entry_price: float, exit_price: float):
        """یادگیری از نتیجه یک معامله برای بهبود تصمیم‌گیری آینده"""
        try:
            profit_percent = ((exit_price - entry_price) / entry_price) * 100
            
            # محاسبه امتیاز (بر اساس سود/زیان)
            if profit_percent > 10:
                score = 10  # عالی
            elif profit_percent > 5:
                score = 7   # خوب
            elif profit_percent > 0:
                score = 5   # متوسط
            elif profit_percent > -5:
                score = 3   # بد
            else:
                score = 1   # خیلی بد
            
            feedback = f"Entry: {entry_price:.2f}, Exit: {exit_price:.2f}, Profit: {profit_percent:.2f}%"
            
            # ذخیره در دیتابیس
            if self.repository is not None:
                self.repository.save_ai_performance(
                    symbol=symbol,
                    decision=decision,
                    expected_profit=profit_percent,
                    actual_profit=profit_percent,
                    score=score,
                    feedback=feedback
                )
                log.info(f"🧠 AI learned from trade: {symbol} -> {profit_percent:.2f}% (score: {score})")
            else:
                log.warning("📁 Repository not available for learning")
            
            return score
        except Exception as e:
            log.error(f"Learning error: {e}")
            return 0

    def get_learning_summary(self, symbol: str = None) -> str:
        """گرفتن خلاصه عملکرد AI برای یک نماد"""
        if self.repository is None:
            return "📊 داده‌های یادگیری در دسترس نیست"
        
        records = self.repository.get_ai_performance(symbol, limit=20)
        if not records:
            return f"📊 هیچ داده‌ی یادگیری برای {symbol or 'همه نمادها'} وجود ندارد"
        
        total = len(records)
        wins = sum(1 for r in records if r['profit_percent'] > 0)
        win_rate = (wins / total) * 100 if total > 0 else 0
        avg_profit = sum(r['profit_percent'] for r in records) / total if total > 0 else 0
        best = max(r['profit_percent'] for r in records) if records else 0
        worst = min(r['profit_percent'] for r in records) if records else 0
        
        return f"""
📊 **خلاصه عملکرد AI** ({symbol or 'همه نمادها'})
تعداد تصمیمات: {total}
موفقیت: {wins} / {total} ({win_rate:.1f}%)
میانگین سود: {avg_profit:.2f}%
بهترین تصمیم: {best:.2f}%
بدترین تصمیم: {worst:.2f}%
"""
