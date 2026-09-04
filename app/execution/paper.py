import logging
from datetime import datetime

log = logging.getLogger(__name__)

class PaperTradingEngine:
    """موتور معاملاتی شبیه‌سازی‌شده (PAPER) - بدون پول واقعی"""

    def __init__(self):
        self.positions = []
        self.balance = 10000.0  # موجودی شبیه‌سازی‌شده به دلار

    def open_position(self, signal, size_usdt: float, entry_price: float):
        """باز کردن یک معامله شبیه‌سازی‌شده"""
        if size_usdt > self.balance:
            log.warning(f"⚠️ موجودی کافی نیست: {self.balance:.2f} USDT")
            return None

        self.balance -= size_usdt
        position = {
            "symbol": signal.market,
            "side": signal.action.value,
            "size_usdt": size_usdt,
            "entry_price": entry_price,
            "timestamp": datetime.now().isoformat()
        }
        self.positions.append(position)
        log.info(f"✅ معامله شبیه‌سازی‌شده باز شد: {position}")
        return position

    def close_position(self, position, exit_price: float):
        """بستن یک معامله شبیه‌سازی‌شده"""
        if position in self.positions:
            self.positions.remove(position)
            profit = (exit_price - position["entry_price"]) * position["size_usdt"] / position["entry_price"]
            self.balance += position["size_usdt"] + profit
            log.info(f"✅ معامله شبیه‌سازی‌شده بسته شد. سود: {profit:.2f} USDT")
            return profit
        return 0.0

    def get_positions(self):
        """دریافت لیست معاملات باز"""
        return self.positions

    def get_balance(self):
        """دریافت موجودی شبیه‌سازی‌شده"""
        return self.balance
