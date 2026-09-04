import logging
import threading
import time

from app.config.settings import settings
from app.monitoring.health import start_health_server_in_background
from app.bitpin.client import BitpinClient
from app.portfolio.manager import PortfolioManager
from app.portfolio.discovery import MarketDiscovery
from app.intelligence.market_intelligence import MarketIntelligence
from app.intelligence.advisor import AIAdvisor
from app.notifications.telegram import TelegramNotifier
from app.notifications.broadcast import BroadcastNotifier
from app.strategies.initial_strategy import InitialStrategy
from app.risk.manager import RiskManager
from app.execution.paper import PaperTradingEngine
from app.database.repository import Repository
from app.strategies.base import Action
# ===== اضافه کنید =====
from app.chat.handler import ChatHandler
# =====================

log = logging.getLogger("main")


def run_cycle(client, portfolio_mgr, discovery, strategy, risk_mgr, repo, notifier, paper_engine, advisor=None):
    """یک چرخه‌ی کامل: دریافت پرتفولیو، بررسی بازارها، تولید سیگنال و (در صورت تایید ریسک) اجرای معامله."""
    snapshot = portfolio_mgr.fetch_snapshot()
    repo.save_portfolio_snapshot(
        snapshot.total_value_usdt,
        snapshot.available_usdt,
        snapshot.exposure_by_asset(),
    )

    held_assets = list(snapshot.exposure_by_asset().keys())
    markets = discovery.find_watchlist_markets(settings.watchlist, held_assets)

    if not markets:
        log.warning("هیچ بازار قابل معامله‌ای پیدا نشد")
        return

    for asset, symbol in markets.items():
        try:
            ticker = client.get_ticker(symbol)
            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            elif not isinstance(ticker, dict):
                ticker = {}

            signal = strategy.evaluate({"symbol": symbol, "ticker": ticker})
            repo.log_signal(signal)

            if signal.action in (Action.WAIT,):
                continue

            if signal.action == Action.DO_NOT_TRADE:
                notifier.send_do_not_trade(f"{symbol}: {signal.reason}")
                continue

            current_exposure_usdt = snapshot.exposure_by_asset().get(asset, 0.0) * signal.current_price
            total_exposure_usdt = max(snapshot.total_value_usdt - snapshot.available_usdt, 0.0)

            decision = risk_mgr.approve(
                signal=signal,
                portfolio_value_usdt=snapshot.total_value_usdt,
                available_usdt=snapshot.available_usdt,
                current_asset_exposure_usdt=current_exposure_usdt,
                total_exposure_usdt=total_exposure_usdt,
                # هنوز اندپوینت اردربوک پیاده نشده؛ فعلاً به‌عنوان placeholder از min_liquidity استفاده می‌کنیم
                orderbook_liquidity_usdt=settings.min_liquidity,
                estimated_slippage_percent=signal.slippage_percent,
                price_age_seconds=0.0,
            )

            if not decision.approved:
                repo.log_risk_event("REJECTED", f"{symbol}: {decision.reason}")
                log.info(f"سیگنال {symbol} توسط ریسک‌منیجر رد شد: {decision.reason}")
                continue

            notifier.send_signal(
                f"{symbol} {signal.action.value} @ {signal.current_price:,.2f} - {signal.reason}"
            )

            # ===== وصل شد: نظر هوش مصنوعی به‌عنوان تحلیل مکمل، قبل از اجرای معامله =====
            if advisor is not None:
                try:
                    portfolio_amounts = {asset: bal.total for asset, bal in snapshot.balances.items()}
                    opinion = advisor.get_recommendation(
                        {"prices": {symbol: signal.current_price}},
                        portfolio_amounts,
                    )
                    notifier.send_ai_opinion(f"{symbol}: {opinion}")
                except Exception as e:
                    log.warning(f"AI advisor error for {symbol}: {e}")
            # ============================================================================

            if settings.mode == "PAPER":
                position = paper_engine.open_position(signal, decision.max_position_usdt, signal.current_price)
                if position:
                    notifier.send_paper_trade(
                        f"{symbol} {signal.action.value} حجم={decision.max_position_usdt:.2f} USDT @ {signal.current_price:,.2f}"
                    )
            elif settings.mode == "LIVE":
                # هشدار: place_order هنوز پیاده نشده - در LIVE mode فعلاً هیچ سفارشی ارسال نمی‌شود
                msg = f"{symbol}: حالت LIVE فعال است ولی ارسال سفارش واقعی هنوز پیاده‌سازی نشده"
                log.error(msg)
                notifier.send_error(msg)
            else:
                log.info(f"{symbol}: حالت OBSERVE - فقط لاگ، بدون اجرای معامله")

        except Exception as e:
            log.exception(f"خطا در پردازش {symbol}: {e}")
            notifier.send_error(f"{symbol}: {e}")


def intelligence_loop(intelligence, notifier, portfolio_mgr, interval_seconds):
    """حلقه‌ی دوره‌ای تحلیل هوشمند بازار و ارسال گزارش/فرصت‌ها به تلگرام."""
    while True:
        try:
            snapshot = portfolio_mgr.fetch_snapshot()
            portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}

            report = intelligence.analyze(portfolio)
            notifier.send_intelligence_report(report["summary"])

            for opp in report.get("opportunities", []):
                notifier.send_opportunity(
                    f"{opp['symbol']}: {opp['action']} @ {opp['price']:,.2f} - {opp['reason']}"
                )
        except Exception as e:
            log.exception(f"خطا در حلقه‌ی هوش بازار: {e}")
            notifier.send_error(f"Intelligence loop error: {e}")

        time.sleep(interval_seconds)

# ===== تابع جدید =====
def chat_loop(telegram, client, discovery, advisor, portfolio_mgr):
    """حلقه‌ی دریافت و پاسخ به پیام‌های تلگرام"""
    engine = MarketIntelligence(settings)
    watchlist = [a.strip().upper() for a in settings.watchlist if isinstance(settings.watchlist, list)]
    handler = ChatHandler(client, discovery, engine, watchlist, advisor=advisor, portfolio_mgr=portfolio_mgr)
    offset = None
    log.info("🤖 Telegram chat handler started")
    while True:
        updates = telegram.get_updates(offset=offset, timeout=25)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message")
            if not message or "text" not in message:
                continue
            chat_id = str(message["chat"]["id"])
            text = message["text"]
            log.info(f"Chat message from {chat_id}: {text}")
            reply = handler.handle(chat_id, text)
            telegram.send_to(chat_id, reply)
        if not updates:
            time.sleep(1)
# ====================

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

    advisor = AIAdvisor(settings)
    intelligence = MarketIntelligence(settings)
    threading.Thread(
        target=intelligence_loop,
        args=(intelligence, notifier, portfolio_mgr, settings.intelligence_interval_seconds),
        daemon=True,
    ).start()
    log.info("🧠 Market Intelligence started")

    # ===== اضافه کنید =====
    if settings.telegram_chat_enabled and telegram.enabled:
        threading.Thread(
            target=chat_loop,
            args=(telegram, client, discovery, advisor, portfolio_mgr),
            daemon=True,
            name="telegram-chat",
        ).start()
        log.info("📨 Telegram chat handler started")
    else:
        log.info("Telegram chat disabled")
    # =====================

    log.info("✅ Bot started successfully")

    while True:
        try:
            run_cycle(client, portfolio_mgr, discovery, strategy, risk_mgr, repo, notifier, paper_engine, advisor)
        except Exception as e:
            log.exception(f"Cycle error: {e}")
        time.sleep(settings.poll_interval_seconds)

if __name__ == "__main__":
    main()
