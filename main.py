import logging
import threading
import time

from app.config.settings import settings
from app.monitoring.health import start_health_server_in_background
from app.bitpin.client import BitpinClient
from app.portfolio.manager import PortfolioManager
from app.portfolio.discovery import MarketDiscovery
from app.intelligence.market_intelligence import MarketIntelligence
from app.notifications.telegram import TelegramNotifier
from app.notifications.broadcast import BroadcastNotifier
from app.strategies.initial_strategy import InitialStrategy
from app.risk.manager import RiskManager
from app.execution.paper import PaperTradingEngine
from app.database.repository import Repository

log = logging.getLogger("main")


def run_cycle(client, portfolio_mgr, discovery, strategy, risk_mgr, repo, notifier, paper_engine):
    try:
        snapshot = portfolio_mgr.fetch_snapshot()
    except Exception as e:
        log.error(f"Portfolio fetch failed: {e}")
        return

    log.info(f"📊 Portfolio: {snapshot.total_value_usdt:.2f} USDT")

    held = [a for a in snapshot.balances if snapshot.balances[a].total > 0]
    tradable = discovery.find_tradable_markets(held)

    for asset, symbol in tradable.items():
        try:
            ticker = client.get_ticker(symbol)
            if ticker:
                signal = strategy.evaluate({"symbol": symbol, "ticker": ticker[0]})
                if signal.action.value != "WAIT":
                    notifier.send_signal(f"📈 {signal.market} - {signal.action.value} - {signal.reason}")
        except Exception as e:
            log.warning(f"Market error for {symbol}: {e}")


def intelligence_loop(intelligence, notifier, interval):
    while True:
        try:
            result = intelligence.analyze({})
            notifier.send_intelligence_report(result["recommendation"])
            for opp in result.get("opportunities", []):
                notifier.send_opportunity(f"{opp['symbol']}: {opp['action']} at {opp['price']:,.2f}")
            time.sleep(interval)
        except Exception as e:
            log.error(f"Intelligence error: {e}")
            time.sleep(60)


def main():
    from app.monitoring.logger import setup_logging
    setup_logging()

    start_health_server_in_background()

    errors = settings.validate()
    if errors:
        for e in errors:
            log.error(f"Config error: {e}")
        return

    log.info(f"🚀 AI Trading Bot in {settings.mode} mode")
    log.info(f"📋 Watchlist: {settings.watchlist}")
    log.info(f"🧠 AI: {'Enabled' if settings.ai_enabled else 'Disabled'}")

    client = BitpinClient(settings.bitpin_base_url, settings.bitpin_api_key, settings.bitpin_api_secret)
    portfolio_mgr = PortfolioManager(client)
    discovery = MarketDiscovery(client)
    strategy = InitialStrategy()
    risk_mgr = RiskManager(settings)
    paper_engine = PaperTradingEngine()
    repo = Repository(settings.database_path)

    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    notifier = BroadcastNotifier(telegram)

    intelligence = MarketIntelligence(settings)
    threading.Thread(target=intelligence_loop, args=(intelligence, notifier, settings.intelligence_interval_seconds), daemon=True).start()
    log.info("🧠 Market Intelligence started")

    log.info("✅ Bot started successfully")

    while True:
        try:
            run_cycle(client, portfolio_mgr, discovery, strategy, risk_mgr, repo, notifier, paper_engine)
        except Exception as e:
            log.exception(f"Cycle error: {e}")
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
