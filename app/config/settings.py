"""
Central configuration. Everything is loaded from environment variables
(or a local .env file via python-dotenv). Nothing sensitive is hard-coded.
"""
import os
from dataclasses import dataclass, field
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

    # --- Polling ---
    poll_interval_seconds: int = field(default_factory=lambda: _int("POLL_INTERVAL_SECONDS", 15))
    stale_data_seconds: int = field(default_factory=lambda: _int("STALE_DATA_SECONDS", 60))

    # --- Telegram ---
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # --- SMS ---
    sms_provider: str = field(default_factory=lambda: os.getenv("SMS_PROVIDER", ""))
    sms_api_key: str = field(default_factory=lambda: os.getenv("SMS_API_KEY", ""))
    sms_api_secret: str = field(default_factory=lambda: os.getenv("SMS_API_SECRET", ""))
    sms_phone_number: str = field(default_factory=lambda: os.getenv("SMS_PHONE_NUMBER", ""))

    # --- Database ---
    database_path: str = field(default_factory=lambda: os.getenv("DATABASE_PATH", "trading_bot.db"))

    def validate(self):
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
        return errors


settings = Settings()
