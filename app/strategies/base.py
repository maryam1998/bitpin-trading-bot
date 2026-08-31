"""
Strategy interface. Strategy logic never lives inside the Bitpin client —
strategies only consume market data and produce Signal objects.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    DO_NOT_TRADE = "DO NOT TRADE"


@dataclass
class Signal:
    asset: str
    market: str
    action: Action
    current_price: float
    entry_price: float
    gross_edge_percent: float
    fee_percent: float
    spread_cost_percent: float
    slippage_percent: float
    safety_margin_percent: float
    reason: str
    risk_level: str = "MEDIUM"

    @property
    def net_edge_percent(self) -> float:
        return (
            self.gross_edge_percent
            - self.fee_percent
            - self.spread_cost_percent
            - self.slippage_percent
            - self.safety_margin_percent
        )


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, market_snapshot: dict) -> Signal:
        """market_snapshot contains ticker/orderbook/trades for one market.
        Must return a Signal — WAIT or DO_NOT_TRADE if conditions aren't met.
        Must never call an LLM to simply ask 'will price go up or down'."""
        raise NotImplementedError
