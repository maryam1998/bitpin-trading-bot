import logging
from .base import BaseStrategy, Signal, Action

log = logging.getLogger(__name__)

class InitialStrategy(BaseStrategy):
    def __init__(self, min_net_edge_percent: float = 0.3):
        self.min_edge = min_net_edge_percent

    def evaluate(self, market_data: dict) -> Signal:
        symbol = market_data.get("symbol", "UNKNOWN")
        ticker = market_data.get("ticker", {})
        
        try:
            last_price = float(ticker.get("price", 0))
        except:
            last_price = 0.0
        
        if last_price <= 0:
            return Signal(market=symbol, action=Action.WAIT, reason="Price unavailable", current_price=0.0)
        
        # تحلیل ساده: اگر قیمت کمتر از ۵۰۰ باشد، خرید
        if last_price < 500 and "USDT" in symbol:
            return Signal(
                market=symbol,
                action=Action.BUY,
                reason=f"Price {last_price:.2f} is low (below 500)",
                current_price=last_price,
                entry_price=last_price * 1.01,
                net_edge_percent=self.min_edge
            )
        
        # اگر قیمت بیشتر از ۵۰۰۰۰ باشد و در پرتفولیو باشد، فروش
        elif last_price > 50000 and "USDT" in symbol:
            return Signal(
                market=symbol,
                action=Action.SELL,
                reason=f"Price {last_price:.2f} is high (above 50000)",
                current_price=last_price,
                entry_price=last_price * 0.99,
                net_edge_percent=self.min_edge
            )
        
        return Signal(market=symbol, action=Action.WAIT, reason="No condition met", current_price=last_price)
