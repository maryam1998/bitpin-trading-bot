from dataclasses import dataclass


@dataclass
class Balance:
    asset: str
    available: float
    frozen: float

    @property
    def total(self) -> float:
        return self.available + self.frozen


@dataclass
class Market:
    symbol: str          # e.g. "BTC_USDT"
    base: str             # e.g. "BTC"
    quote: str             # e.g. "USDT"
    active: bool
    raw: dict


@dataclass
class Ticker:
    symbol: str
    last_price: float
    bid: float
    ask: float
    volume_24h: float
    timestamp: float

    @property
    def spread(self) -> float:
        if self.bid <= 0:
            return float("inf")
        return (self.ask - self.bid) / self.bid
