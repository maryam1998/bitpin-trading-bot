from enum import Enum
from dataclasses import dataclass
from typing import Optional

class Action(Enum):
    WAIT = "WAIT"
    BUY = "BUY"
    SELL = "SELL"
    DO_NOT_TRADE = "DO_NOT_TRADE"

@dataclass
class Signal:
    market: str
    action: Action
    reason: str
    current_price: float
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    net_edge_percent: float = 0.0
    slippage_percent: float = 0.5

class BaseStrategy:
    def evaluate(self, market_data: dict) -> Signal:
        raise NotImplementedError
