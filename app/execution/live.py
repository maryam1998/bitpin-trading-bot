"""
LIVE execution engine.

This is the ONLY module allowed to call BitpinClient.create_order /
cancel_order. It refuses to run unless every safety precondition in
LiveTradingGate.check() is satisfied. No AI/LLM component may call this
directly or change any of these gates.
"""
import logging
import time

from app.bitpin.client import BitpinClient, BitpinAPIError
from app.risk.manager import RiskManager, RiskDecision
from app.strategies.base import Signal, Action

log = logging.getLogger("execution.live")


class LiveTradingBlocked(Exception):
    pass


class LiveTradingGate:
    """All of these must be true before a single real order can be sent."""

    def check(self, settings, risk_manager: RiskManager, balance_ok: bool,
              market_ok: bool, liquidity_ok: bool, spread_ok: bool,
              slippage_ok: bool, net_edge_ok: bool) -> list:
        problems = []
        if settings.mode != "LIVE":
            problems.append("BOT_MODE is not LIVE.")
        if not settings.live_trading:
            problems.append("LIVE_TRADING env var is not true.")
        if not settings.live_confirmed:
            problems.append("LIVE_TRADING_CONFIRMED env var is not true (explicit human confirmation missing).")
        if not (settings.bitpin_api_key and settings.bitpin_api_secret):
            problems.append("Bitpin API credentials are not configured.")
        if risk_manager._kill_switch:
            problems.append("Emergency kill switch is active.")
        if not balance_ok:
            problems.append("Balance check failed.")
        if not market_ok:
            problems.append("Market availability check failed.")
        if not liquidity_ok:
            problems.append("Liquidity check failed.")
        if not spread_ok:
            problems.append("Spread check failed.")
        if not slippage_ok:
            problems.append("Slippage check failed.")
        if not net_edge_ok:
            problems.append("Net edge below minimum.")
        return problems


class LiveExecutionEngine:
    def __init__(self, client: BitpinClient, risk_manager: RiskManager, settings, notifier=None):
        self.client = client
        self.risk_manager = risk_manager
        self.settings = settings
        self.notifier = notifier
        self.gate = LiveTradingGate()

    def execute(self, signal: Signal, risk_decision: RiskDecision, gate_checks: dict):
        problems = self.gate.check(self.settings, self.risk_manager, **gate_checks)
        if problems or not risk_decision.approved:
            reasons = problems + ([risk_decision.reason] if not risk_decision.approved else [])
            log.warning("LIVE order blocked for %s: %s", signal.market, "; ".join(reasons))
            raise LiveTradingBlocked("; ".join(reasons))

        side = "buy" if signal.action == Action.BUY else "sell"
        qty = risk_decision.max_position_usdt / signal.entry_price

        log.critical("SENDING REAL ORDER: %s %s qty=%.8f on %s", side, signal.market, qty, self.settings.bitpin_base_url)
        try:
            order = self.client.create_order(
                symbol=signal.market,
                side=side,
                order_type="limit",
                amount=f"{qty:.8f}",
                price=f"{signal.entry_price:.8f}",
            )
        except BitpinAPIError as e:
            log.error("LIVE order failed: %s", e)
            if self.notifier:
                self.notifier.send_critical(f"⚠️ LIVE order FAILED for {signal.market}: {e}")
            raise

        if self.notifier:
            self.notifier.send_critical(
                f"🔴 LIVE TRADE EXECUTED\n{signal.market}\nSide: {side}\nQty: {qty:.8f}\nPrice: {signal.entry_price}"
            )
        return order
