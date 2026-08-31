"""
Entry point.

OBSERVE mode (default): reads real portfolio + market data, generates
signals, sends notifications. Places ZERO orders.

PAPER mode: same as OBSERVE, but also simulates trades via PaperTradingEngine.
Places ZERO real orders.

LIVE mode: only reachable if every gate in app/execution/live.py passes.
Disabled by default and never auto-activates.
"""
import logging
import time

from app.config.settings import settings
from app.monitoring.logger import setup_logging
from app.bitpin.client import BitpinClient, BitpinAPIError
from app.portfolio.manager import PortfolioManager
from app.portfolio.discovery import MarketDiscovery
from app.strategies.initial_strategy import InitialStrategy
from app.strategies.base import Action
from app.risk.manager import RiskManager
from app.execution.paper import PaperTradingEngine
from app.notifications.telegram import TelegramNotifier
from app.notifications.sms import SMSNotifier
from app.database.repository import Repository

log = logging.getLogger("main")


def build_market_snapshot(client: BitpinClient, asset: str, symbol: str) -> dict:
    ticker_raw = client.get_ticker(symbol)
    t = ticker_raw[0] if isinstance(ticker_raw, list) else ticker_raw
    ticker = {
        "last": t.get("price") or t.get("last") or t.get("last_price"),
        "bid": t.get("bid") or t.get("price"),
        "ask": t.get("ask") or t.get("price"),
        "volume": t.get("volume_24h") or t.get("volume") or 0,
    }
    try:
        orderbook = client.get_orderbook(symbol)
    except BitpinAPIError:
        orderbook = None

    return {
        "asset": asset,
        "symbol": symbol,
        "ticker": ticker,
        "orderbook": orderbook,
        "recent_change_percent": float(t.get("change_percent", t.get("daily_change_percent", 0)) or 0),
        "fetched_at": time.time(),
    }


def run_cycle(client, portfolio_mgr, discovery, strategy, risk_mgr, repo, telegram, sms, paper_engine):
    try:
        snapshot = portfolio_mgr.fetch_snapshot()
    except BitpinAPIError as e:
        log.error("Portfolio fetch failed: %s", e)
        telegram.send_error(f"Portfolio fetch failed: {e}")
        return

    repo.save_portfolio_snapshot(snapshot.total_value_usdt, snapshot.available_usdt, snapshot.exposure_by_asset())
    log.info("Portfolio value: %.2f USDT | Available USDT: %.2f", snapshot.total_value_usdt, snapshot.available_usdt)

    held_assets = [a for a in snapshot.balances if snapshot.balances[a].total > 0]
    tradable = discovery.find_tradable_markets(held_assets)

    for asset, symbol in tradable.items():
        try:
            market_snapshot = build_market_snapshot(client, asset, symbol)
        except BitpinAPIError as e:
            log.warning("Market data fetch failed for %s: %s", symbol, e)
            continue

        signal = strategy.evaluate(market_snapshot)
        repo.log_signal(signal)

        if signal.action == Action.WAIT:
            continue

        report = (
            f"{signal.market}\nSignal: {signal.action.value}\nReason: {signal.reason}\n"
            f"Current price: {signal.current_price}\nExpected net edge: {signal.net_edge_percent:.3f}%"
        )
        log.info("SIGNAL: %s", report.replace("\n", " | "))

        if signal.action == Action.DO_NOT_TRADE:
            telegram.send_do_not_trade(report)
            continue

        telegram.send_opportunity(report)

        if settings.mode == "PAPER":
            exposure = snapshot.exposure_by_asset()
            decision = risk_mgr.approve(
                signal,
                portfolio_value_usdt=snapshot.total_value_usdt,
                available_usdt=snapshot.available_usdt,
                current_asset_exposure_usdt=exposure.get(asset, 0.0),
                total_exposure_usdt=sum(exposure.values()),
                orderbook_liquidity_usdt=settings.min_liquidity,  # TODO: compute from real orderbook depth
                estimated_slippage_percent=signal.slippage_percent,
                price_age_seconds=time.time() - market_snapshot["fetched_at"],
            )
            repo.log_risk_event("paper_decision", f"{signal.market}: approved={decision.approved} {decision.reason}")
            if decision.approved:
                pos = paper_engine.open_position(signal, decision.max_position_usdt, signal.entry_price)
                telegram.send_paper_trade(f"Opened {signal.action.value} {signal.market} size={decision.max_position_usdt:.2f} USDT")
            else:
                log.info("Paper trade rejected by RiskManager: %s", decision.reason)

        elif settings.mode == "LIVE":
            log.critical(
                "LIVE mode signal generated for %s but this build's main.py "
                "does not wire up LiveExecutionEngine yet — no order sent. "
                "Wire app/execution/live.py deliberately once Phase 2 is reviewed.",
                signal.market,
            )


def main():
    setup_logging()
    errors = settings.validate()
    if errors:
        for e in errors:
            log.error("Config error: %s", e)
        if settings.mode == "LIVE":
            log.critical("Refusing to start in LIVE mode due to config errors above.")
            return

    log.info("Starting Bitpin trading bot in %s mode", settings.mode)
    if settings.mode == "LIVE":
        log.critical("LIVE MODE IS ACTIVE. Real orders can be sent once wired up.")

    client = BitpinClient(settings.bitpin_base_url, settings.bitpin_api_key, settings.bitpin_api_secret)
    portfolio_mgr = PortfolioManager(client)
    discovery = MarketDiscovery(client, min_liquidity_usdt=settings.min_liquidity)
    strategy = InitialStrategy(min_net_edge_percent=settings.min_net_edge)
    risk_mgr = RiskManager(settings)
    paper_engine = PaperTradingEngine()
    repo = Repository(settings.database_path)
    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    sms = SMSNotifier(settings.sms_provider, settings.sms_api_key, settings.sms_api_secret, settings.sms_phone_number)

    repo.log_system_event("startup", f"mode={settings.mode}")

    while True:
        run_cycle(client, portfolio_mgr, discovery, strategy, risk_mgr, repo, telegram, sms, paper_engine)
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
