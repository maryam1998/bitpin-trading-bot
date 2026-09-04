from abc import ABC, abstractmethod
from typing import Dict, Any, List


class MarketProvider(ABC):
    """کلاس پایه برای تمام تأمین‌کنندگان داده بازار"""

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        pass

    @abstractmethod
    def get_historical(self, symbol: str, days: int = 30) -> List[Dict]:
        pass

    @abstractmethod
    def get_market_overview(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        pass
