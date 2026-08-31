"""
Risk Manager.

Every trade — paper or live — MUST pass through RiskManager.approve().
No strategy, execution module, or AI component may bypass this.
"""
import logging
import time
from dataclasses import dataclass

from app.strategies.base import Signal, Action

log = logging.getLogger("risk.manager")


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    max_position_usdt: float = 0.0


class KillSwitchTripped(Exception):
    pass


class RiskManager:
    def __init__(self, settings):
        self.settings = settings
        self._daily_loss_usdt = 0.0
        self._daily_start_value_usdt = None
        self._day_start_ts = time.time()
        self._consecutive_losses = 0
        self._open_positions = 0
        self._kill_switch = False

    def trip_kill_switch(self, reason: str):
        self._kill_switch = True
        log.critical("EMERGENCY KILL SWITCH TRIPPED: %s", reason)

    def reset_kill_switch(self):
        self._kill_switch = False

    def record_trade_result(self, pnl_usdt: float):
        if pnl_usdt < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        self._daily_loss_usdt += min(0.0, pnl_usdt) * -1  # track losses as positive number

    def _maybe_roll_day(self, portfolio_value_usdt: float):
        if self._daily_start_value_usdt is None or time.time() - self._day_start_ts > 86400:
            self._daily_start_value_usdt = portfolio_value_usdt
            self._day_start_ts = time.time()
            self._daily_loss_usdt = 0.0

    def approve(self, signal: Signal, portfolio_value_usdt: float, available_usdt: float,
                current_asset_exposure_usdt: float, total_exposure_usdt: float,
                orderbook_liquidity_usdt: float, estimated_slippage_percent: float,
                price_age_seconds: float) -> RiskDecision:

        s = self.settings
        self._maybe_roll_day(portfolio_value_usdt)

        if self._kill_switch:
            return RiskDecision(False, "Emergency kill switch is active. No new trades.")

        if signal.action not in (Action.BUY, Action.SELL):
            return RiskDecision(False, f"Signal action is {signal.action}, nothing to approve.")

        if price_age_seconds > s.stale_data_seconds:
            return RiskDecision(False, f"Price data is stale ({price_age_seconds:.0f}s old).")

        if signal.net_edge_percent < s.min_net_edge:
            return RiskDecision(False, f"Net edge {signal.net_edge_percent:.3f}% below minimum {s.min_net_edge}%.")

        if orderbook_liquidity_usdt < s.min_liquidity:
            return RiskDecision(False, f"Liquidity {orderbook_liquidity_usdt:.0f} USDT below minimum {s.min_liquidity}.")

        if estimated_slippage_percent > s.max_slippage_percent:
            return RiskDecision(False, f"Estimated slippage {estimated_slippage_percent:.3f}% exceeds max {s.max_slippage_percent}%.")

        if self._open_positions >= s.max_open_positions:
            return RiskDecision(False, f"Max open positions ({s.max_open_positions}) reached.")

        if self._consecutive_losses >= s.max_consecutive_losses:
            return RiskDecision(False, f"Max consecutive losses ({s.max_consecutive_losses}) reached — pausing.")

        if self._daily_start_value_usdt:
            daily_loss_percent = 100.0 * self._daily_loss_usdt / self._daily_start_value_usdt
            if daily_loss_percent >= s.max_daily_loss_percent:
                self.trip_kill_switch(f"Daily loss {daily_loss_percent:.2f}% reached limit {s.max_daily_loss_percent}%")
                return RiskDecision(False, "Daily loss limit reached. All new trades stopped.")

        max_position_usdt = portfolio_value_usdt * (s.max_position_percent / 100.0)
        max_position_usdt = min(max_position_usdt, available_usdt)

        prospective_asset_exposure = current_asset_exposure_usdt + max_position_usdt
        if portfolio_value_usdt > 0:
            asset_exposure_percent = 100.0 * prospective_asset_exposure / portfolio_value_usdt
            if asset_exposure_percent > s.max_asset_exposure_percent:
                return RiskDecision(False, f"Would exceed max asset exposure ({s.max_asset_exposure_percent}%).")

            prospective_total_exposure = total_exposure_usdt + max_position_usdt
            total_exposure_percent = 100.0 * prospective_total_exposure / portfolio_value_usdt
            if total_exposure_percent > s.max_total_exposure_percent:
                return RiskDecision(False, f"Would exceed max total exposure ({s.max_total_exposure_percent}%).")

        if max_position_usdt <= 0:
            return RiskDecision(False, "Calculated position size is zero (insufficient available balance).")

        return RiskDecision(True, "Approved.", max_position_usdt=max_position_usdt)
