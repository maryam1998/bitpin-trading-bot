import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Bitpin
    bitpin_api_key: str = os.getenv("BITPIN_API_KEY", "")
    bitpin_api_secret: str = os.getenv("BITPIN_API_SECRET", "")
    bitpin_base_url: str = os.getenv("BITPIN_BASE_URL", "https://api.bitpin.ir")

    # Mode
    mode: str = os.getenv("BOT_MODE", "PAPER").upper()

    # AI
    ai_enabled: bool = os.getenv("AI_ENABLED", "true").lower() == "true"
    ai_provider: str = os.getenv("AI_PROVIDER", "openai")
    ai_model: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    ai_api_key: str = os.getenv("AI_API_KEY", "")

    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_chat_enabled: bool = os.getenv("TELEGRAM_CHAT_ENABLED", "true").lower() == "true"

    # Market Data APIs
    brsapi_url: str = os.getenv("BRSAPI_URL", "https://api.brsapi.ir/Market/Gold_Currency.php")
    coingecko_api_key: str = os.getenv("COINGECKO_API_KEY", "")

    # Watchlist (همه بازارها)
    watchlist: List[str] = field(default_factory=lambda: [
        "BTC", "ETH", "USDT", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LINK",
        "GOLD", "SILVER", "USD_IRT", "EUR_IRT", "GBP_IRT"
    ])

    # Risk
    max_position_percent: float = float(os.getenv("MAX_POSITION_PERCENT", "5.0"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "5"))
    min_net_edge: float = float(os.getenv("MIN_NET_EDGE", "0.3"))
    min_liquidity: float = float(os.getenv("MIN_LIQUIDITY", "1000"))

    # Polling
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    intelligence_interval_seconds: int = int(os.getenv("INTELLIGENCE_INTERVAL_SECONDS", "300"))
    opportunity_check_interval: int = int(os.getenv("OPPORTUNITY_CHECK_INTERVAL", "300"))  # جدید

    # Database
    database_path: str = os.getenv("DATABASE_PATH", "trading_bot.db")

    def validate(self) -> List[str]:
        errors = []
        if self.mode not in ("OBSERVE", "PAPER", "LIVE"):
            errors.append("BOT_MODE must be OBSERVE, PAPER or LIVE")
        if self.ai_enabled and not self.ai_api_key:
            errors.append("AI_ENABLED=true but AI_API_KEY is missing")
        if not self.telegram_bot_token or not self.telegram_chat_id:
            errors.append("Telegram credentials are missing")
        return errors


settings = Settings()
