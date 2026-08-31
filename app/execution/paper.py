"""
Paper trading engine. Uses real Bitpin market data but NEVER sends a real
order. All fills, fees and slippage are simulated.
"""
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.strategies.base import Signal, Action

log = logging.getLogger("execution.paper")

SIMULATED_FEE_PERCENT = 0.35   # matches strategy's fee assumption; adjust
                                 # once real Bitpin fee schedule is confirmed.


@dataclass
class PaperPosition:
    asset: str
    market: str
    side: str
    entry_price: float
    size_usdt: float
    opened_at: float
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class PaperTradeResult:
    position: PaperPosition
    exit_price: float
    gross_pnl_usdt: float
    fees_usdt: float
    net_pnl_usdt: float


class PaperTradingEngine:
    def __init__(self):
        self.open_positions: dict = {}
        self.closed_trades: list = []

    def open_position(self, signal: Signal, size_usdt: float, fill_price: float) -> PaperPosition:
        pos = PaperPosition(
            asset=signal.asset,
            market=signal.market,
            side=signal.action.value,
            entry_price=fill_price,
            size_usdt=size_usdt,
            opened_at=time.time(),
        )
        self.open_positions[pos.id] = pos
        log.info("[PAPER] Opened %s %s size=%.2f USDT @ %.6f", pos.side, pos.market, size_usdt, fill_price)
        return pos

    def close_position(self, position_id: str, exit_price: float) -> PaperTradeResult:
        pos = self.open_positions.pop(position_id)
        qty = pos.size_usdt / pos.entry_price
        if pos.side == Action.BUY.value:
            gross_pnl = qty * (exit_price - pos.entry_price)
        else:
            gross_pnl = qty * (pos.entry_price - exit_price)
        fees = pos.size_usdt * (SIMULATED_FEE_PERCENT / 100.0) * 2  # entry+exit
        net_pnl = gross_pnl - fees
        result = PaperTradeResult(pos, exit_price, gross_pnl, fees, net_pnl)
        self.closed_trades.append(result)
        log.info("[PAPER] Closed %s net_pnl=%.4f USDT", pos.market, net_pnl)
        return result

    def stats(self) -> dict:
        n = len(self.closed_trades)
        wins = [t for t in self.closed_trades if t.net_pnl_usdt > 0]
        losses = [t for t in self.closed_trades if t.net_pnl_usdt <= 0]
        gross_pnl = sum(t.gross_pnl_usdt for t in self.closed_trades)
        net_pnl = sum(t.net_pnl_usdt for t in self.closed_trades)
        fees = sum(t.fees_usdt for t in self.closed_trades)
        gross_profit = sum(t.net_pnl_usdt for t in wins)
        gross_loss = abs(sum(t.net_pnl_usdt for t in losses))
        return {
            "total_trades": n,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_percent": 100.0 * len(wins) / n if n else 0.0,
            "gross_pnl_usdt": gross_pnl,
            "net_pnl_usdt": net_pnl,
            "fees_usdt": fees,
            "average_trade_usdt": net_pnl / n if n else 0.0,
            "profit_factor": (gross_profit / gross_loss) if gross_loss else float("inf") if gross_profit else 0.0,
        }
