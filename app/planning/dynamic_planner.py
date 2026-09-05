import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
import numpy as np

log = logging.getLogger(__name__)

@dataclass
class TradingPlan:
    symbol: str
    action: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_percent: float
    time_horizon: int
    reason: str
    confidence: float


class DynamicPlanner:
    def __init__(self, settings, market_data_manager, portfolio_manager, advisor):
        self.settings = settings
        self.market_data = market_data_manager
        self.portfolio = portfolio_manager
        self.advisor = advisor
        self._plans = {}
        self._active_plans = {}
        self._historical_plans = []

    def _get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        """دریافت داده‌های تاریخی با استفاده از منابع موجود"""
        try:
            # استفاده از CoinGeckoProvider یا BitpinProvider
            for provider in self.market_data.providers:
                if provider.supports_symbol(symbol):
                    data = provider.get_historical(symbol, days)
                    if data and len(data) > 5:
                        return data
            return []
        except Exception as e:
            log.warning(f"Could not get historical data for {symbol}: {e}")
            return []

    def analyze_market_conditions(self) -> Dict[str, Any]:
        """تحلیل شرایط کلی بازار"""
        try:
            btc_price = self.market_data.get_price("BTC")
            eth_price = self.market_data.get_price("ETH")
            gold_price = self.market_data.get_price("GOLD")
            dollar_price = self.market_data.get_price("USD_IRT")

            btc_historical = self._get_historical("BTC", days=30)

            volatility = self._calculate_volatility(btc_historical)
            trend = self._detect_trend(btc_historical)

            market_condition = {
                "btc_price": btc_price,
                "eth_price": eth_price,
                "gold_price": gold_price,
                "dollar_price": dollar_price,
                "volatility": volatility,
                "trend": trend,
                "timestamp": datetime.now().isoformat(),
                "market_state": self._determine_market_state(volatility, trend),
            }

            log.info(f"📊 Market conditions: {market_condition['market_state']} | Volatility: {volatility:.2f}")
            return market_condition

        except Exception as e:
            log.error(f"Market analysis error: {e}")
            return {
                "volatility": 0.3,
                "trend": "neutral",
                "market_state": "unknown",
                "timestamp": datetime.now().isoformat(),
            }

    def _calculate_volatility(self, historical_data: List[Dict]) -> float:
        try:
            if not historical_data or len(historical_data) < 10:
                return 0.3

            prices = [p.get("price", 0) for p in historical_data[-30:] if p.get("price", 0) > 0]
            if len(prices) < 10:
                return 0.3

            mean_price = np.mean(prices)
            if mean_price == 0:
                return 0.3

            variance = np.var(prices)
            std_dev = np.sqrt(variance)
            volatility = std_dev / mean_price
            return max(0.05, min(1.0, volatility))

        except Exception as e:
            log.error(f"Volatility calculation error: {e}")
            return 0.3

    def _detect_trend(self, historical_data: List[Dict]) -> str:
        try:
            if not historical_data or len(historical_data) < 20:
                return "neutral"

            prices = [p.get("price", 0) for p in historical_data[-20:] if p.get("price", 0) > 0]
            if len(prices) < 10:
                return "neutral"

            x = list(range(len(prices)))
            slope = np.polyfit(x, prices, 1)[0]
            slope_percent = slope / prices[0] if prices[0] > 0 else 0

            if slope_percent > 0.03:
                return "strong_bullish"
            elif slope_percent > 0.01:
                return "bullish"
            elif slope_percent < -0.03:
                return "strong_bearish"
            elif slope_percent < -0.01:
                return "bearish"
            else:
                return "neutral"

        except Exception as e:
            log.error(f"Trend detection error: {e}")
            return "neutral"

    def _determine_market_state(self, volatility: float, trend: str) -> str:
        if volatility > 0.5:
            if trend in ["bullish", "strong_bullish"]:
                return "high_volatility_bullish"
            elif trend in ["bearish", "strong_bearish"]:
                return "high_volatility_bearish"
            else:
                return "high_volatility_neutral"

        elif volatility < 0.15:
            if trend in ["bullish", "strong_bullish"]:
                return "low_volatility_bullish"
            elif trend in ["bearish", "strong_bearish"]:
                return "low_volatility_bearish"
            else:
                return "low_volatility_neutral"

        else:
            if trend in ["bullish", "strong_bullish"]:
                return "normal_bullish"
            elif trend in ["bearish", "strong_bearish"]:
                return "normal_bearish"
            else:
                return "normal_neutral"

    def generate_trading_plan(self, symbol: str) -> Optional[TradingPlan]:
        try:
            market_condition = self.analyze_market_conditions()
            volatility = market_condition.get("volatility", 0.3)
            market_state = market_condition.get("market_state", "normal_neutral")

            price = self.market_data.get_price(symbol)
            if price <= 0:
                log.warning(f"Invalid price for {symbol}, skipping plan generation")
                return None

            try:
                from app.forecast.report import ForecastReport
                forecast = ForecastReport()
                report = forecast.generate_full_report(symbol, days=14)
                recommendation = report.get("recommendation", "HOLD")
                predicted_price = report.get("prediction", {}).get("predicted_price", price)
                confidence = report.get("confidence", 0.6)
            except Exception as e:
                log.warning(f"Forecast unavailable for {symbol}: {e}")
                recommendation = "HOLD"
                predicted_price = price
                confidence = 0.5

            if volatility > 0.5:
                position_size = 3.0
                time_horizon = 12
            elif volatility < 0.15:
                position_size = 8.0
                time_horizon = 72
            else:
                position_size = 5.0
                time_horizon = 48

            if volatility > 0.5:
                sl_multiplier = 0.12
                tp_multiplier = 0.20
            elif volatility < 0.15:
                sl_multiplier = 0.04
                tp_multiplier = 0.08
            else:
                sl_multiplier = 0.08
                tp_multiplier = 0.15

            trend = market_condition.get("trend", "neutral")

            if recommendation in ["BUY", "STRONG_BUY"] and trend in ["bullish", "strong_bullish"]:
                action = "BUY"
                entry_price = price * 0.995
                stop_loss = price * (1 - sl_multiplier)
                take_profit = price * (1 + tp_multiplier)
                reason = f"AI recommends BUY, market bullish with volatility {volatility:.2f}"

            elif recommendation in ["SELL", "STRONG_SELL"] and trend in ["bearish", "strong_bearish"]:
                action = "SELL"
                entry_price = price * 1.005
                stop_loss = price * (1 + sl_multiplier)
                take_profit = price * (1 - tp_multiplier)
                reason = f"AI recommends SELL, market bearish with volatility {volatility:.2f}"

            elif recommendation in ["BUY", "STRONG_BUY"] and trend in ["neutral"]:
                action = "BUY"
                entry_price = price * 0.998
                stop_loss = price * (1 - sl_multiplier * 0.7)
                take_profit = price * (1 + tp_multiplier * 0.7)
                reason = f"AI recommends BUY, market neutral, controlled risk"

            elif recommendation in ["SELL", "STRONG_SELL"] and trend in ["neutral"]:
                action = "SELL"
                entry_price = price * 1.002
                stop_loss = price * (1 + sl_multiplier * 0.7)
                take_profit = price * (1 - tp_multiplier * 0.7)
                reason = f"AI recommends SELL, market neutral, controlled risk"

            else:
                log.info(f"No clear opportunity for {symbol}, holding")
                return None

            plan = TradingPlan(
                symbol=symbol,
                action=action,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                position_size_percent=position_size,
                time_horizon=time_horizon,
                reason=reason,
                confidence=confidence,
            )

            self._plans[symbol] = plan
            self._active_plans[symbol] = {
                "plan": plan,
                "created_at": datetime.now(),
                "status": "active",
                "market_condition": market_condition,
            }

            log.info(f"📋 Generated trading plan for {symbol}: {action} @ {entry_price:.2f} | Risk: {position_size:.1f}%")
            return plan

        except Exception as e:
            log.error(f"Plan generation error for {symbol}: {e}")
            return None

    def adjust_plan(self, symbol: str) -> Optional[TradingPlan]:
        if symbol not in self._plans:
            return None

        old_plan = self._plans[symbol]
        market_condition = self.analyze_market_conditions()

        plan_time = self._active_plans.get(symbol, {}).get("created_at")
        if plan_time:
            age_hours = (datetime.now() - plan_time).total_seconds() / 3600
            if age_hours > old_plan.time_horizon:
                log.info(f"⏰ Plan for {symbol} expired (age: {age_hours:.1f}h)")
                self.expire_plan(symbol)
                return None

        volatility = market_condition.get("volatility", 0.3)
        old_volatility = self._active_plans.get(symbol, {}).get("market_condition", {}).get("volatility", 0.3)

        if abs(volatility - old_volatility) > 0.1:
            new_position_size = old_plan.position_size_percent

            if volatility > 0.5:
                new_position_size = max(2.0, old_plan.position_size_percent * 0.7)
            elif volatility < 0.15:
                new_position_size = min(10.0, old_plan.position_size_percent * 1.3)

            if new_position_size != old_plan.position_size_percent:
                adjusted_plan = TradingPlan(
                    symbol=symbol,
                    action=old_plan.action,
                    entry_price=old_plan.entry_price,
                    stop_loss=old_plan.stop_loss,
                    take_profit=old_plan.take_profit,
                    position_size_percent=new_position_size,
                    time_horizon=old_plan.time_horizon,
                    reason=f"Adjusted due to volatility change: {volatility:.2f} (was {old_volatility:.2f})",
                    confidence=old_plan.confidence * 0.9,
                )
                self._plans[symbol] = adjusted_plan
                self._active_plans[symbol]["plan"] = adjusted_plan
                self._active_plans[symbol]["market_condition"] = market_condition
                log.info(f"🔧 Adjusted plan for {symbol}: position {old_plan.position_size_percent:.1f}% → {new_position_size:.1f}%")
                return adjusted_plan

        return None

    def apply_plan_to_signal(self, symbol: str, signal) -> Optional[Dict[str, Any]]:
        """اعمال پلن به سیگنال (حد ضرر، حد سود، حجم)"""
        if symbol not in self._plans:
            return None

        plan = self._plans[symbol]
        active = self._active_plans.get(symbol, {})

        if active.get("status") != "active":
            return None

        # تنظیم signal با مقادیر پلن
        signal.stop_loss = plan.stop_loss
        signal.take_profit = plan.take_profit
        signal.entry_price = plan.entry_price

        return {
            "stop_loss": plan.stop_loss,
            "take_profit": plan.take_profit,
            "entry_price": plan.entry_price,
            "position_size_percent": plan.position_size_percent,
            "reason": plan.reason,
        }

    def expire_plan(self, symbol: str):
        if symbol in self._active_plans:
            self._active_plans[symbol]["status"] = "expired"
            log.info(f"⏰ Plan expired for {symbol}")

    def get_active_plans(self) -> Dict[str, Any]:
        return {k: v for k, v in self._active_plans.items() if v.get("status") == "active"}
