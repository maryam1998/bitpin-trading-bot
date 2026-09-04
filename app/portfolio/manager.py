import logging
from typing import Dict, List, Any
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

@dataclass
class AssetBalance:
    total: float
    available: float
    frozen: float

@dataclass
class PortfolioSnapshot:
    total_value_irt: float = 0.0
    total_value_usdt: float = 0.0
    available_usdt: float = 0.0
    balances: Dict[str, AssetBalance] = field(default_factory=dict)
    percentages: Dict[str, float] = field(default_factory=dict)
    irit_balance: float = 0.0  # موجودی نقدی IRT

    def exposure_by_asset(self) -> Dict[str, float]:
        return {asset: bal.total for asset, bal in self.balances.items() if bal.total > 0}

class PortfolioManager:
    def __init__(self, client):
        self.client = client
        self._price_cache = {}

    def _get_ticker_price(self, symbol: str) -> float:
        """دریافت قیمت دقیق برای یک symbol از بیت‌پین"""
        try:
            tickers = self.client.get_ticker(symbol)
            if not tickers:
                log.warning(f"No ticker data for {symbol}")
                return 0.0
            
            # اگر لیست است، تیکر دقیق را پیدا کن
            if isinstance(tickers, list):
                for t in tickers:
                    if t.get("symbol") == symbol:
                        return float(t.get("price", 0))
                # اگر پیدا نشد، از اولین استفاده نکن، بلکه صفر برگردان
                log.warning(f"Exact ticker for {symbol} not found in response")
                return 0.0
            elif isinstance(tickers, dict):
                return float(tickers.get("price", 0))
            else:
                return 0.0
        except Exception as e:
            log.error(f"Error fetching price for {symbol}: {e}")
            return 0.0

    def _get_usdt_irt_price(self) -> float:
        """قیمت لحظه‌ای USDT/IRT"""
        return self._get_ticker_price("USDT_IRT")

    def _get_asset_irt_price(self, asset: str) -> float:
        """قیمت هر دارایی بر حسب IRT (از طریق USDT یا مستقیم)"""
        if asset == "USDT":
            return self._get_usdt_irt_price()
        elif asset == "IRT":
            return 1.0  # IRT خودش واحد پول است
        
        # ابتدا سعی کن با IRT
        price = self._get_ticker_price(f"{asset}_IRT")
        if price > 0:
            return price
        
        # سپس با USDT
        usdt_price = self._get_ticker_price(f"{asset}_USDT")
        if usdt_price > 0:
            usdt_irt = self._get_usdt_irt_price()
            return usdt_price * usdt_irt
        
        return 0.0

    def fetch_snapshot(self) -> PortfolioSnapshot:
        """دریافت وضعیت کیف پول و محاسبه دقیق ارزش و درصدها"""
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            if not wallets:
                log.error("No wallet data received")
                return PortfolioSnapshot()

            balances = {}
            irit_balance = 0.0
            usdt_balance = 0.0
            asset_values_irt = {}  # ارزش هر دارایی به IRT
            asset_balances = {}    # موجودی هر دارایی

            # ۱. استخراج موجودی‌ها
            for item in wallets:
                asset = item.get("asset", "")
                total = float(item.get("balance", 0))
                available = float(item.get("available", 0))
                frozen = float(item.get("frozen", 0))
                
                if total == 0:
                    continue
                
                balances[asset] = AssetBalance(total=total, available=available, frozen=frozen)
                asset_balances[asset] = total
                
                if asset == "IRT":
                    irit_balance = total
                elif asset == "USDT":
                    usdt_balance = total

            # ۲. دریافت قیمت‌ها
            usdt_irt_price = self._get_usdt_irt_price()
            if usdt_irt_price <= 0:
                log.warning("USDT/IRT price is zero, using fallback")
                # fallback: از آخرین قیمت معتبر استفاده کن (اگر داری) یا ۱
                usdt_irt_price = 1.0

            # ۳. محاسبه ارزش هر دارایی به IRT
            total_value_irt = 0.0
            
            for asset, balance in asset_balances.items():
                if asset == "IRT":
                    value_irt = balance  # خودش IRT است
                elif asset == "USDT":
                    value_irt = balance * usdt_irt_price
                else:
                    price_irt = self._get_asset_irt_price(asset)
                    value_irt = balance * price_irt
                
                if value_irt > 0:
                    asset_values_irt[asset] = value_irt
                    total_value_irt += value_irt

            # ۴. محاسبه درصدها (نسبت به کل)
            percentages = {}
            for asset, value in asset_values_irt.items():
                if total_value_irt > 0:
                    percentages[asset] = (value / total_value_irt) * 100
                else:
                    percentages[asset] = 0.0

            # ۵. محاسبه ارزش کل به USDT (برای سازگاری با سایر بخش‌ها)
            total_value_usdt = total_value_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0
            available_usdt = usdt_balance  # موجودی USDT آزاد

            # ۶. ساخت snapshot
            snapshot = PortfolioSnapshot(
                total_value_irt=total_value_irt,
                total_value_usdt=total_value_usdt,
                available_usdt=available_usdt,
                balances=balances,
                percentages=percentages,
                irit_balance=irit_balance
            )

            # ۷. لاگ برای بررسی
            log.info(f"Portfolio calculated: total IRT={total_value_irt:,.2f}, USDT={total_value_usdt:.2f}")
            for asset, pct in percentages.items():
                if pct > 0.01:
                    log.info(f"  {asset}: {pct:.2f}%")

            return snapshot

        except Exception as e:
            log.exception(f"Error fetching portfolio snapshot: {e}")
            return PortfolioSnapshot()
