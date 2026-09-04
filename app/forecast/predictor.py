import logging
import numpy as np
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from statsmodels.tsa.arima.model import ARIMA
from sklearn.linear_model import LinearRegression

log = logging.getLogger(__name__)

class Predictor:
    """پیش‌بینی قیمت با استفاده از مدل‌های سری‌زمانی"""

    def __init__(self):
        self.model = None
        self._cache = {}

    def _extract_prices(self, historical: List[Dict]) -> np.ndarray:
        """استخراج قیمت‌ها از داده‌های تاریخی"""
        if not historical:
            return np.array([])
        prices = [item["price"] for item in historical]
        return np.array(prices)

    def _extract_timestamps(self, historical: List[Dict]) -> np.ndarray:
        """استخراج زمان‌ها از داده‌های تاریخی"""
        if not historical:
            return np.array([])
        timestamps = [item["time"] for item in historical]
        return np.array(timestamps)

    def forecast_arima(self, historical: List[Dict], steps: int = 30) -> List[float]:
        """
        پیش‌بینی با مدل ARIMA
        steps: تعداد روزهای آینده برای پیش‌بینی
        """
        if len(historical) < 30:
            log.warning("Not enough historical data for ARIMA (need at least 30 points)")
            return []

        prices = self._extract_prices(historical)
        if len(prices) < 30:
            return []

        try:
            # مدل ARIMA با پارامترهای پایه
            model = ARIMA(prices, order=(5, 1, 2))
            model_fit = model.fit()
            forecast = model_fit.forecast(steps=steps)
            return forecast.tolist()
        except Exception as e:
            log.error(f"ARIMA forecast error: {e}")
            return []

    def forecast_linear_regression(self, historical: List[Dict], steps: int = 30) -> List[float]:
        """
        پیش‌بینی با رگرسیون خطی ساده
        """
        if len(historical) < 10:
            return []

        prices = self._extract_prices(historical)
        if len(prices) < 10:
            return []

        # آماده‌سازی داده‌ها
        X = np.arange(len(prices)).reshape(-1, 1)
        y = prices

        # مدل رگرسیون خطی
        model = LinearRegression()
        model.fit(X, y)

        # پیش‌بینی
        last_idx = len(prices)
        X_future = np.arange(last_idx, last_idx + steps).reshape(-1, 1)
        forecast = model.predict(X_future)
        return forecast.tolist()

    def forecast_moving_average(self, historical: List[Dict], window: int = 14, steps: int = 30) -> List[float]:
        """
        پیش‌بینی با میانگین متحرک ساده (با شیب)
        """
        if len(historical) < window:
            return []

        prices = self._extract_prices(historical)
        if len(prices) < window:
            return []

        # محاسبه میانگین متحرک
        ma = np.convolve(prices, np.ones(window)/window, mode='valid')
        last_ma = ma[-1] if len(ma) > 0 else prices[-1]

        # محاسبه شیب روند
        if len(ma) > 2:
            slope = (ma[-1] - ma[-2]) / 2
        else:
            slope = 0

        # پیش‌بینی
        forecast = []
        for i in range(1, steps + 1):
            predicted = last_ma + slope * i
            # اضافه کردن نویز تصادفی برای واقع‌گرایی
            noise = np.random.normal(0, last_ma * 0.005)
            forecast.append(predicted + noise)

        return forecast

    def forecast_ensemble(self, historical: List[Dict], steps: int = 30) -> Dict[str, Any]:
        """
        پیش‌بینی با ترکیب مدل‌ها (Ensemble)
        """
        if not historical or len(historical) < 20:
            return {"error": "Not enough historical data", "forecast": []}

        # دریافت پیش‌بینی از هر مدل
        arima_forecast = self.forecast_arima(historical, steps)
        lr_forecast = self.forecast_linear_regression(historical, steps)
        ma_forecast = self.forecast_moving_average(historical, steps=steps)

        # ترکیب (وزن‌دهی)
        forecasts = []
        weights = []

        if arima_forecast:
            forecasts.append(arima_forecast)
            weights.append(0.5)  # وزن بیشتر برای ARIMA

        if lr_forecast:
            forecasts.append(lr_forecast)
            weights.append(0.3)

        if ma_forecast:
            forecasts.append(ma_forecast)
            weights.append(0.2)

        if not forecasts:
            return {"error": "No model could generate forecast", "forecast": []}

        # نرمال‌سازی وزن‌ها
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]

        # ترکیب پیش‌بینی‌ها
        combined = []
        for i in range(steps):
            value = 0
            for j, f in enumerate(forecasts):
                if i < len(f):
                    value += f[i] * weights[j]
            combined.append(value)

        # محاسبه قیمت نهایی
        last_price = historical[-1]["price"] if historical else 0
        predicted_price = combined[-1] if combined else last_price

        return {
            "forecast": combined,
            "predicted_price": predicted_price,
            "change_percent": ((predicted_price - last_price) / last_price) * 100 if last_price > 0 else 0,
            "last_price": last_price,
            "models_used": len(forecasts),
        }

    def generate_prediction_report(self, symbol: str, historical: List[Dict], steps: int = 30) -> Dict[str, Any]:
        """تولید گزارش کامل پیش‌بینی"""
        if not historical:
            return {"error": "No historical data available", "symbol": symbol}

        # اجرای پیش‌بینی ترکیبی
        result = self.forecast_ensemble(historical, steps)

        if "error" in result:
            return {"error": result["error"], "symbol": symbol}

        last_price = historical[-1]["price"] if historical else 0
        forecast = result.get("forecast", [])
        predicted_price = result.get("predicted_price", last_price)
        change_percent = result.get("change_percent", 0)

        # تعیین جهت
        if change_percent > 5:
            direction = "📈 صعودی (خرید)"
            recommendation = "BUY"
        elif change_percent < -5:
            direction = "📉 نزولی (فروش)"
            recommendation = "SELL"
        else:
            direction = "↔️ خنثی (نگهداری)"
            recommendation = "HOLD"

        return {
            "symbol": symbol,
            "last_price": last_price,
            "predicted_price": predicted_price,
            "change_percent": change_percent,
            "direction": direction,
            "recommendation": recommendation,
            "forecast": forecast[:10],  # ۱۰ روز اول
            "confidence": "بالا" if len(forecast) > 10 else "متوسط",
            "models_used": result.get("models_used", 0),
            "timestamp": datetime.now().isoformat(),
        }
