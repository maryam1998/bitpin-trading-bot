"""
Initial strategy — deterministic and rule-based, NOT an LLM guess.

Uses momentum, spread, order-book imbalance and volume. Nothing here claims
to be profitable; it only decides whether the *expected net edge* clears a
configurable minimum threshold, per the spec.
"""
import logging
from app.strategies.base import Strategy, Signal, Action

log = logging.getLogger("strategy.initial")

TAKER_FEE_PERCENT = 0.35   # Bitpin fee schedule — UNVERIFIED, confirm current
                            # maker/taker fees on docs.bitpin.ir before LIVE use.
SAFETY_MARGIN_PERCENT = 0.1


class InitialStrategy(Strategy):
    name = "momentum_orderbook_v1"

    def __init__(self, min_net_edge_percent: float = 0.3, momentum_window_percent: float = 0.5):
        self.min_net_edge_percent = min_net_edge_percent
        self.momentum_window_percent = momentum_window_percent

    def evaluate(self, market_snapshot: dict) -> Signal:
        asset = market_snapshot["asset"]
        symbol = market_snapshot["symbol"]
        ticker = market_snapshot["ticker"]           # dict: last, bid, ask, volume
        orderbook = market_snapshot.get("orderbook")   # dict: bids, asks
        recent_change_percent = market_snapshot.get("recent_change_percent", 0.0)

        last_price = float(ticker["last"])
        bid = float(ticker["bid"])
        ask = float(ticker["ask"])
        spread_percent = 100.0 * (ask - bid) / bid if bid else 100.0

        imbalance = 0.0
        if orderbook:
            bid_vol = sum(float(b[1]) for b in orderbook.get("bids", [])[:10])
            ask_vol = sum(float(a[1]) for a in orderbook.get("asks", [])[:10])
            total = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / total if total else 0.0

        # Gross edge estimate: momentum strength scaled by order-book support.
        gross_edge = abs(recent_change_percent) * (0.5 + 0.5 * abs(imbalance))
        slippage_estimate = spread_percent * 0.5

        signal_direction = Action.WAIT
        if recent_change_percent > self.momentum_window_percent and imbalance > 0.1:
            signal_direction = Action.BUY
        elif recent_change_percent < -self.momentum_window_percent and imbalance < -0.1:
            signal_direction = Action.SELL

        sig = Signal(
            asset=asset,
            market=symbol,
            action=signal_direction,
            current_price=last_price,
            entry_price=ask if signal_direction == Action.BUY else bid,
            gross_edge_percent=gross_edge,
            fee_percent=TAKER_FEE_PERCENT,
            spread_cost_percent=spread_percent,
            slippage_percent=slippage_estimate,
            safety_margin_percent=SAFETY_MARGIN_PERCENT,
            reason=(
                f"momentum={recent_change_percent:.2f}% imbalance={imbalance:.2f} "
                f"spread={spread_percent:.3f}%"
            ),
        )

        if sig.action == Action.WAIT:
            sig.reason = "No sufficient momentum/imbalance signal."
            return sig

        if sig.net_edge_percent < self.min_net_edge_percent:
            sig.action = Action.DO_NOT_TRADE
            sig.reason += f" | net edge {sig.net_edge_percent:.3f}% below minimum {self.min_net_edge_percent}%"

        return sig
