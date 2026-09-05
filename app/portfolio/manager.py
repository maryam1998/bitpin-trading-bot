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
    irit_balance: float = 0.0

    def exposure_by_asset(self) -> Dict[str, float]:
        return {asset: bal.total for asset, bal in self.balances.items() if bal.total > 0}

class PortfolioManager:
    def __init__(self, client):
        self.client = client
        self._price_cache = {}

    def _get_ticker_price(self, symbol: str) -> float:
        """
        دریافت قیمت یک نماد (مثلاً 'BTC_USDT') از بیت‌پین، با کش کوتاه‌مدت
        تا در یک snapshot درخواست‌های تکراری زده نشود.
        """
        if symbol in self._price_cache:
            return self._price_cache[symbol]

        price = 0.0
        try:
            ticker = self.client.get_ticker(symbol)
            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            if isinstance(ticker, dict):
                price = float(ticker.get("price", 0) or 0)
        except Exception as e:
            log.warning(f"خطا در دریافت قیمت {symbol}: {e}")
            price = 0.0

        self._price_cache[symbol] = price
        return price

    def _get_usdt_irt_price(self) -> float:
        """قیمت تتر به تومان"""
        return self._get_ticker_price("USDT_IRT")

    def _get_asset_irt_price(self, asset: str) -> float:
        """
        قیمت یک دارایی به تومان.
        ابتدا از بازار {asset}_USDT استفاده و به تومان تبدیل می‌شود،
        و در صورت نبود آن بازار، از {asset}_IRT مستقیم استفاده می‌شود.
        """
        usdt_irt = self._get_usdt_irt_price()
        price_usdt = self._get_ticker_price(f"{asset}_USDT")
        if price_usdt > 0 and usdt_irt > 0:
            return price_usdt * usdt_irt

        price_irt = self._get_ticker_price(f"{asset}_IRT")
        if price_irt > 0:
            return price_irt

        return 0.0

    def fetch_snapshot(self) -> PortfolioSnapshot:
        # کش قیمت‌ها فقط در طول یک snapshot معتبر است تا قیمت‌ها همیشه به‌روز باشند
        self._price_cache = {}
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            if not wallets:
                log.error("No wallet data received")
                return PortfolioSnapshot()

            # لاگ تشخیصی: اگه بازم مغایرت بود، این خط دقیقاً نشون می‌ده API
            # چند ردیف برای هر دارایی برگردونده (کمک به دیباگ بعدی)
            log.info(f"📦 wallets raw rows: {len(wallets)} | assets: {[w.get('asset') for w in wallets]}")

            balances = {}
            irit_balance = 0.0
            usdt_balance = 0.0
            asset_values_irt = {}
            asset_balances = {}

            # ===== اصلاح مهم: جمع‌کردن (نه جایگزینی) موجودی وقتی چند ردیف
            # برای یک دارایی برمی‌گرده =====
            # مستندات SDKهای غیررسمی بیت‌پین نشون میدن این endpoint می‌تونه
            # بر اساس "service" (مثلاً spot در مقابل انواع دیگر) چند ردیف
            # جدا برای یک ارز برگردونه. کد قبلی با balances[asset] = ...
            # هر ردیف رو جایگزین ردیف قبلی می‌کرد، یعنی اگه دو ردیف USDT
            # وجود داشت، فقط آخری حساب می‌شد و بقیه‌ی موجودی واقعی کاربر
            # گم می‌شد - همین باعث می‌شد جمع کل همیشه کمتر از عدد واقعی
            # اپ بیت‌پین باشه.
            for item in wallets:
                asset = item.get("asset", "")
                total = float(item.get("balance", 0))
                frozen = float(item.get("frozen", 0))
                # ===== اصلاح: محاسبه available در صورت نبودن فیلد =====
                available = float(item.get("available", total - frozen))

                if total == 0:
                    continue

                if asset in balances:
                    existing = balances[asset]
                    balances[asset] = AssetBalance(
                        total=existing.total + total,
                        available=existing.available + available,
                        frozen=existing.frozen + frozen,
                    )
                else:
                    balances[asset] = AssetBalance(total=total, available=available, frozen=frozen)
                asset_balances[asset] = balances[asset].total

                if asset == "IRT":
                    irit_balance = balances[asset].total
                elif asset == "USDT":
                    usdt_balance = balances[asset].available

            # ... ادامه محاسبه قیمت‌ها و درصدها (بدون تغییر) ...
            usdt_irt_price = self._get_usdt_irt_price()
            if usdt_irt_price <= 0:
                usdt_irt_price = 1.0

            total_value_irt = 0.0
            for asset, balance in asset_balances.items():
                if asset == "IRT":
                    value_irt = balance
                elif asset == "USDT":
                    value_irt = balance * usdt_irt_price
                else:
                    price_irt = self._get_asset_irt_price(asset)
                    value_irt = balance * price_irt
                if value_irt > 0:
                    asset_values_irt[asset] = value_irt
                    total_value_irt += value_irt

            percentages = {}
            for asset, value in asset_values_irt.items():
                percentages[asset] = (value / total_value_irt) * 100 if total_value_irt > 0 else 0.0

            total_value_usdt = total_value_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0

            return PortfolioSnapshot(
                total_value_irt=total_value_irt,
                total_value_usdt=total_value_usdt,
                available_usdt=usdt_balance,
                balances=balances,
                percentages=percentages,
                irit_balance=irit_balance,
            )

        except Exception as e:
            log.exception(f"Error fetching portfolio snapshot: {e}")
            return PortfolioSnapshot()
