import logging
from typing import Optional, Dict, Any
from datetime import datetime

log = logging.getLogger(__name__)

class LiveExecutionEngine:
    """موتور اجرای معاملات واقعی در بیت‌پین"""

    def __init__(self, client):
        self.client = client
        self._active_orders = {}  # tracking order_id -> info
        self._positions = []       # positions history

    def execute_signal(self, signal, size_usdt: float, entry_price: float = None) -> Optional[Dict[str, Any]]:
        """
        اجرای سیگنال معاملاتی به‌صورت واقعی
        
        Args:
            signal: شیء Signal از استراتژی
            size_usdt: حجم معامله به USDT
            entry_price: قیمت ورود (اگر None باشد، از قیمت بازار استفاده می‌شود)
        
        Returns:
            اطلاعات سفارش ثبت‌شده یا None در صورت خطا
        """
        symbol = signal.market
        side = "buy" if signal.action.value == "BUY" else "sell"
        
        try:
            # ۱. دریافت قیمت لحظه‌ای (برای سفارش بازار)
            ticker = self.client.get_ticker(symbol)
            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            
            current_price = float(ticker.get("price", 0))
            if current_price <= 0:
                log.error(f"❌ Invalid price for {symbol}: {current_price}")
                return None
            
            # ۲. محاسبه مقدار (به واحد base asset)
            price_to_use = entry_price or current_price
            amount = size_usdt / price_to_use
            
            # ۳. ارسال سفارش بازار (برای سادگی)
            order_type = "market"
            response = self.client.place_order(
                symbol=symbol,
                side=side,
                order_type=order_type,
                amount=amount,
                price=None,  # بازار
            )
            
            # ۴. ثبت سفارش در تاریخچه
            order_info = {
                "order_id": response.get("id", "unknown"),
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": current_price,
                "size_usdt": size_usdt,
                "status": "filled",
                "timestamp": datetime.now().isoformat(),
            }
            self._positions.append(order_info)
            
            log.info(f"✅ Order placed: {side} {amount:.6f} {symbol} @ {current_price:.2f}")
            return order_info
            
        except Exception as e:
            log.error(f"❌ Live execution error for {symbol}: {e}")
            return None

    def get_positions(self) -> list:
        """دریافت لیست معاملات باز (از تاریخچه)"""
        return self._positions

    def get_active_orders(self) -> dict:
        """دریافت سفارشات فعال"""
        return self._active_orders

    def close_position(self, symbol: str, amount: float = None) -> Optional[Dict[str, Any]]:
        """
        بستن یک پوزیشن (فروش کامل یا جزئی)
        """
        if amount is None:
            # اگر مقدار مشخص نشده، از موجودی موجود استفاده کن
            # (در عمل باید از کیف پول بخوانی)
            amount = 0.0  # placeholder

        try:
            response = self.client.place_order(
                symbol=symbol,
                side="sell",
                order_type="market",
                amount=amount,
            )
            log.info(f"✅ Position closed: {amount} {symbol}")
            return response
        except Exception as e:
            log.error(f"❌ Close position error: {e}")
            return None
