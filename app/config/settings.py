import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    val = os.getenv(name)
    try:
        return float(val) if val not in (None, "") else default
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val not in (None, "") else default
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Bitpin credentials (never logged, never hard-coded) ---
    bitpin_api_key: str = field(default_factory=lambda: os.getenv("BITPIN_API_KEY", ""))
    bitpin_api_secret: str = field(default_factory=lambda: os.getenv("BITPIN_API_SECRET", ""))
    bitpin_base_url: str = field(default_factory=lambda: os.getenv("BITPIN_BASE_URL", "https://api.bitpin.ir"))

    # --- Mode ---
    # OBSERVE (default) | PAPER | LIVE
    mode: str = field(default_factory=lambda: os.getenv("BOT_MODE", "OBSERVE").upper())
    live_trading: bool = field(default_factory=lambda: _bool("LIVE_TRADING", False))
    live_confirmed: bool = field(default_factory=lambda: _bool("LIVE_TRADING_CONFIRMED", False))

    # --- Risk limits (all configurable, no hard-coded trading behavior) ---
    max_position_percent: float = field(default_factory=lambda: _float("MAX_POSITION_PERCENT", 5.0))
    max_asset_exposure_percent: float = field(default_factory=lambda: _float("MAX_ASSET_EXPOSURE_PERCENT", 20.0))
    max_total_exposure_percent: float = field(default_factory=lambda: _float("MAX_TOTAL_EXPOSURE_PERCENT", 60.0))
    max_daily_loss_percent: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_PERCENT", 3.0))
    max_open_positions: int = field(default_factory=lambda: _int("MAX_OPEN_POSITIONS", 5))
    max_slippage_percent: float = field(default_factory=lambda: _float("MAX_SLIPPAGE_PERCENT", 0.5))
    min_liquidity: float = field(default_factory=lambda: _float("MIN_LIQUIDITY", 1000.0))
    min_net_edge: float = field(default_factory=lambda: _float("MIN_NET_EDGE", 0.3))
    max_consecutive_losses: int = field(default_factory=lambda: _int("MAX_CONSECUTIVE_LOSSES", 4))

    # --- Polling & Intervals ---
    poll_interval_seconds: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 60))
    stale_data_seconds: int = field(default_factory=lambda: _int("STALE_DATA_SECONDS", 60))
    intelligence_interval_seconds: int = field(default_factory=lambda: _int("INTELLIGENCE_INTERVAL_SECONDS", 300))
    opportunity_check_interval: int = field(default_factory=lambda: _int("OPPORTUNITY_CHECK_INTERVAL", 300))
    signal_cooldown_seconds: int = field(default_factory=lambda: _int("SIGNAL_COOLDOWN_SECONDS", 1800))  # ۳۰ دقیقه

    # --- AI ---
    ai_enabled: bool = field(default_factory=lambda: _bool("AI_ENABLED", True))
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "openai"))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "gpt-4o-mini"))
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))

    # ===== قابلیت‌های جدید Agent خودمختار =====
    active_learning_enabled: bool = field(default_factory=lambda: _bool("ACTIVE_LEARNING_ENABLED", True))
    dynamic_planning_enabled: bool = field(default_factory=lambda: _bool("DYNAMIC_PLANNING_ENABLED", True))
    adaptive_risk_enabled: bool = field(default_factory=lambda: _bool("ADAPTIVE_RISK_ENABLED", True))

    # --- Telegram ---
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    telegram_chat_enabled: bool = field(default_factory=lambda: _bool("TELEGRAM_CHAT_ENABLED", True))

    # --- SMS ---
    sms_provider: str = field(default_factory=lambda: os.getenv("SMS_PROVIDER", ""))
    sms_api_key: str = field(default_factory=lambda: os.getenv("SMS_API_KEY", ""))
    sms_api_secret: str = field(default_factory=lambda: os.getenv("SMS_API_SECRET", ""))
    sms_phone_number: str = field(default_factory=lambda: os.getenv("SMS_PHONE_NUMBER", ""))

    # --- Market Data APIs ---
    # ===== اصلاح: آدرس قدیمی Gold_Currency.php الان به یک کلید API نیاز دارد
    # و بدون آن همیشه ۰ برمی‌گرداند. آدرس رایگانِ بدون‌نیاز-به-کلید جایگزین شد.
    # اگر کلید Pro داری، BRSAPI_URL و BRSAPI_KEY را در تنظیمات ست کن.
    brsapi_url: str = field(default_factory=lambda: os.getenv(
        "BRSAPI_URL", "https://brsapi.ir/FreeTsetmcBourseApi/Api_Free_Gold_Currency_v2.json"
    ))
    brsapi_key: str = field(default_factory=lambda: os.getenv("BRSAPI_KEY", ""))
    coingecko_api_key: str = field(default_factory=lambda: os.getenv("COINGECKO_API_KEY", ""))

    # --- Strategy ---
    strategy: str = field(default_factory=lambda: os.getenv("STRATEGY", "initial"))

    # --- Watchlist (همه بازارها) ---
    watchlist: List[str] = field(default_factory=lambda: [
        "BTC", "ETH", "USDT", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LINK",
        "GOLD", "SILVER", "USD_IRT", "EUR_IRT", "GBP_IRT", "SHIB", "TRX"
    ])

    # --- Database ---
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "trading_bot.db"))

    def validate(self) -> List[str]:
        errors = []
        if self.mode not in ("OBSERVE", "PAPER", "LIVE"):
            errors.append(f"BOT_MODE must be OBSERVE, PAPER or LIVE (got {self.mode!r})")

        if self.mode == "LIVE":
            if not self.live_trading:
                errors.append("LIVE mode requires LIVE_TRADING=true")
            if not self.live_confirmed:
                errors.append("LIVE mode requires LIVE_TRADING_CONFIRMED=true (explicit human confirmation)")
            if not (self.bitpin_api_key and self.bitpin_api_secret):
                errors.append("LIVE mode requires BITPIN_API_KEY and BITPIN_API_SECRET")

        if self.ai_enabled and not self.ai_api_key:
            errors.append("AI_ENABLED=true but AI_API_KEY is missing")

        if not self.telegram_bot_token or not self.telegram_chat_id:
            errors.append("Telegram credentials are missing (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")

        return errors


settings = Settings()
