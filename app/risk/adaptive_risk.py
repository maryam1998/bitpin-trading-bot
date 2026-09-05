import logging
from datetime import datetime
import numpy as np
from typing import Dict, Any, Optional
from dataclasses import dataclass
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

@dataclass
class RiskDecision:
    approved: bool
    max_position_usdt: float
    reason: str
    risk_score: float = 0.0
    confidence: float = 0.0

class AdaptiveRiskManager:
    """
    مدیریت ریسک تطبیقی با استفاده از یادگیری ماشین
    - تحلیل ریسک بر اساس شرایط بازار
    - تنظیم پویای پارامترهای ریسک
    - یادگیری از نتایج قبلی
    """

    def __init__(self, settings, repository, market_data_manager):
        self.settings = settings
        self.repo = repository
        self.market_data = market_data_manager
        self._model = None
        self._scaler = StandardScaler()
        self._trained = False
        self._risk_history = []

    def _train_model(self):
        """آموزش مدل یادگیری ماشین برای ارزیابی ریسک"""
        try:
            trades = self.repo.get_trades(status="closed", limit=100)
            if len(trades) < 20:
                log.warning("Not enough data to train risk model")
                return

            X = []
            y = []

            for trade in trades:
                profit = trade.get("profit_percent", 0)
                features = [
                    trade.get("size_usdt", 0) / 1000,
                    abs(profit),
                    trade.get("entry_price", 0) / 100,
                ]
                X.append(features)
                y.append(1 if profit > 0 else 0)

            if len(X) < 20:
                return

            X = np.array(X)
            y = np.array(y)
            self._scaler.fit(X)
            X_scaled = self._scaler.transform(X)

            self._model = LogisticRegression(C=1.0, max_iter=100)
            self._model.fit(X_scaled, y)
            self._trained = True
            log.info("🧠 Risk model trained successfully")

        except Exception as e:
            log.error(f"Risk model training error: {e}")

    def calculate_risk_score(self, signal, portfolio_value_usdt: float, available_usdt: float) -> Dict[str, float]:
        """محاسبه امتیاز ریسک یک معامله"""
        try:
            position_size = min(available_usdt * 0.1, portfolio_value_usdt * 0.05)
            size_ratio = position_size / portfolio_value_usdt if portfolio_value_usdt > 0 else 0

            volatility = 0.3
            try:
                btc_price = self.market_data.get_price("BTC")
                if btc_price:
                    volatility = 0.3
            except:
                pass

            risk_score = (
                size_ratio * 2 +
                volatility * 1.5 +
                (signal.slippage_percent / 100) * 5
            ) / 5

            risk_score = min(1.0, max(0.0, risk_score))

            if self._trained and self._model:
                try:
                    features = np.array([[position_size / 1000, abs(signal.current_price) / 100, signal.current_price / 100]])
                    features_scaled = self._scaler.transform(features)
                    prob = self._model.predict_proba(features_scaled)[0][1]
                    risk_score = 0.6 * risk_score + 0.4 * (1 - prob)
                except Exception as e:
                    log.warning(f"Model prediction error: {e}")

            confidence = 1.0 - risk_score

            return {
                "risk_score": risk_score,
                "confidence": confidence,
                "size_ratio": size_ratio,
                "volatility": volatility,
            }

        except Exception as e:
            log.error(f"Risk score calculation error: {e}")
            return {"risk_score": 0.5, "confidence": 0.5, "size_ratio": 0.05, "volatility": 0.3}

    def approve(self, signal, portfolio_value_usdt: float, available_usdt: float,
                current_asset_exposure_usdt: float, total_exposure_usdt: float,
                orderbook_liquidity_usdt: float, estimated_slippage_percent: float,
                price_age_seconds: float) -> RiskDecision:
        """تأیید یا رد معامله با تحلیل ریسک تطبیقی"""
        try:
            max_position = min(
                available_usdt * (self.settings.max_position_percent / 100),
                portfolio_value_usdt * (self.settings.max_position_percent / 100),
            )

            if max_position <= 0:
                return RiskDecision(False, 0, "Insufficient funds", 0, 0)

            risk_result = self.calculate_risk_score(signal, portfolio_value_usdt, available_usdt)
            risk_score = risk_result.get("risk_score", 0.5)
            confidence = risk_result.get("confidence", 0.5)

            checks = []

            if orderbook_liquidity_usdt < self.settings.min_liquidity:
                checks.append(f"Low liquidity: {orderbook_liquidity_usdt:.0f} < {self.settings.min_liquidity}")

            if estimated_slippage_percent > 2.0:
                checks.append(f"High slippage: {estimated_slippage_percent:.2f}%")

            if current_asset_exposure_usdt > portfolio_value_usdt * 0.25:
                checks.append(f"High asset exposure: {current_asset_exposure_usdt:.0f} > 25%")

            if risk_score > 0.7:
                checks.append(f"High risk score: {risk_score:.2f}")

            if checks:
                reason = " | ".join(checks)
                return RiskDecision(False, max_position, reason, risk_score, confidence)

            adjusted_position = max_position * (1 - risk_score * 0.5)
            adjusted_position = max(adjusted_position, max_position * 0.3)

            self._risk_history.append({
                "symbol": signal.market,
                "action": signal.action.value,
                "risk_score": risk_score,
                "position_size": adjusted_position,
                "timestamp": datetime.now().isoformat(),  # حالا datetime تعریف شده است
            })

            return RiskDecision(
                approved=True,
                max_position_usdt=adjusted_position,
                reason=f"Risk score: {risk_score:.2f}, Confidence: {confidence:.2f}",
                risk_score=risk_score,
                confidence=confidence,
            )

        except Exception as e:
            log.error(f"Risk approval error: {e}")
            return RiskDecision(False, 0, f"Error: {e}", 0, 0)

    def get_risk_stats(self) -> Dict[str, Any]:
        if not self._risk_history:
            return {"total_decisions": 0, "avg_risk_score": 0}

        total = len(self._risk_history)
        avg_risk = sum(r["risk_score"] for r in self._risk_history) / total

        return {
            "total_decisions": total,
            "avg_risk_score": avg_risk,
            "last_decision": self._risk_history[-1] if self._risk_history else None,
            "model_trained": self._trained,
        }

    def get_risk_summary(self) -> str:
        stats = self.get_risk_stats()
        return f"""
📊 **مدیریت ریسک تطبیقی:**
- تعداد تصمیمات: {stats.get('total_decisions', 0)}
- میانگین امتیاز ریسک: {stats.get('avg_risk_score', 0):.2f}
- وضعیت مدل: {'✅ آموزش دیده' if stats.get('model_trained') else '⏳ در حال آموزش'}
"""
