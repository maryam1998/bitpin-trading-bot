from dataclasses import dataclass

@dataclass
class RiskDecision:
    approved: bool
    max_position_usdt: float
    reason: str

class RiskManager:
    def __init__(self, settings):
        self.settings = settings

    def approve(self, signal, portfolio_value_usdt: float, available_usdt: float,
                current_asset_exposure_usdt: float, total_exposure_usdt: float,
                orderbook_liquidity_usdt: float, estimated_slippage_percent: float,
                price_age_seconds: float) -> RiskDecision:
        
        # محاسبه حداکثر حجم معامله
        max_position = min(
            available_usdt * (self.settings.max_position_percent / 100),
            portfolio_value_usdt * (self.settings.max_position_percent / 100)
        )
        
        # بررسی ریسک‌ها
        if max_position <= 0:
            return RiskDecision(False, 0, "Insufficient funds")
        
        if orderbook_liquidity_usdt < self.settings.min_liquidity:
            return RiskDecision(False, 0, "Low liquidity")
        
        if estimated_slippage_percent > 1.0:
            return RiskDecision(False, 0, "High slippage")
        
        return RiskDecision(True, max_position, "Risk check passed")
