"""
ربات تریدینگ هوشمند - نسخه نهایی با AI Agent، اجرای واقعی، یادگیری و ارسال خودکار فرصت‌ها
"""
import logging
import threading
import time

from app.config.settings import settings
from app.monitoring.health import start_health_server_in_background
from app.bitpin.client import BitpinClient
from app.portfolio.manager import PortfolioManager
from app.portfolio.discovery import MarketDiscovery
from app.market_data.manager import MarketDataManager
from app.intelligence.market_intelligence import MarketIntelligence
from app.intelligence.advisor import AIAdvisor
from app.notifications.telegram import TelegramNotifier
from app.notifications.broadcast import BroadcastNotifier
from app.strategies.initial_strategy import InitialStrategy
from app.risk.manager import RiskManager
from app.execution.paper import PaperTradingEngine
from app.execution.live import LiveExecutionEngine
from app.database.repository import Repository
from app.strategies.base import Action
from app.chat.handler import ChatHandler
from app.forecast.report import ForecastReport  # جدید

log = logging.getLogger("main")


def run_cycle(
    client,
    portfolio_mgr,
    discovery,
    strategy,
    risk_mgr,
    repo,
    notifier,
    paper_engine,
    live_engine,
    advisor=None,
):
    """
    یک چرخه‌ی کامل:
    1. دریافت وضعیت کیف پول
    2. پیدا کردن بازارهای قابل معامله
    3. تصمیم‌گیری با AI (یا fallback)
    4. تأیید ریسک
    5. اجرای معامله (PAPER / LIVE / OBSERVE)
    6. ارسال نظر AI و گزارش به تلگرام
    """
    try:
        snapshot = portfolio_mgr.fetch_snapshot()
    except Exception as e:
        log.error(f"خطا در دریافت کیف پول: {e}")
        notifier.send_error(f"خطا در دریافت کیف پول: {e}")
        return

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
            # ===== ۱. تصمیم‌گیری با AI یا قانون =====
            if advisor is not None and settings.ai_enabled:
                signal = advisor.decide(asset, symbol)
                log.info(f"🤖 AI decision for {symbol}: {signal.action.value} - {signal.reason}")
            else:
                # Fallback به استراتژی قانون‌محور
                ticker = client.get_ticker(symbol)
                if isinstance(ticker, list):
                    ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
                elif not isinstance(ticker, dict):
                    ticker = {}
                signal = strategy.evaluate({"symbol": symbol, "ticker": ticker})
                log.info(f"📊 Fallback strategy for {symbol}: {signal.action.value} - {signal.reason}")
            # =======================================

            repo.log_signal(signal)

            if signal.action in (Action.WAIT, Action.DO_NOT_TRADE):
                continue

            # ===== ۲. محاسبه مواجهه و تأیید ریسک =====
            current_exposure_usdt = snapshot.exposure_by_asset().get(asset, 0.0) * signal.current_price
            total_exposure_usdt = max(snapshot.total_value_usdt - snapshot.available_usdt, 0.0)

            decision = risk_mgr.approve(
                signal=signal,
                portfolio_value_usdt=snapshot.total_value_usdt,
                available_usdt=snapshot.available_usdt,
                current_asset_exposure_usdt=current_exposure_usdt,
                total_exposure_usdt=total_exposure_usdt,
                orderbook_liquidity_usdt=settings.min_liquidity,
                estimated_slippage_percent=signal.slippage_percent,
                price_age_seconds=0.0,
            )

            if not decision.approved:
                repo.log_risk_event("REJECTED", f"{symbol}: {decision.reason}")
                log.info(f"⛔ سیگنال {symbol} توسط ریسک‌منیجر رد شد: {decision.reason}")
                notifier.send_do_not_trade(f"{symbol}: {decision.reason}")
                continue
            # =======================================

            # ===== ۳. ارسال سیگنال و نظر AI به تلگرام =====
            notifier.send_signal(
                f"{symbol} {signal.action.value} @ {signal.current_price:,.2f} - {signal.reason}"
            )

            if advisor is not None:
                try:
                    portfolio_amounts = {asset: bal.total for asset, bal in snapshot.balances.items()}
                    opinion = advisor.get_recommendation(
                        {"prices": {symbol: signal.current_price}},
                        portfolio_amounts,
                    )
                    notifier.send_ai_opinion(f"{symbol}: {opinion}")
                except Exception as e:
                    log.warning(f"⚠️ AI advisor error for {symbol}: {e}")
            # ============================================

            # ===== ۴. اجرای معامله =====
            if settings.mode == "PAPER":
                position = paper_engine.open_position(signal, decision.max_position_usdt, signal.current_price)
                if position:
                    notifier.send_paper_trade(
                        f"📄 {symbol} {signal.action.value} حجم={decision.max_position_usdt:.2f} USDT @ {signal.current_price:,.2f}"
                    )
                else:
                    notifier.send_error(f"❌ PAPER trade failed for {symbol}")

            elif settings.mode == "LIVE":
                if live_engine is not None:
                    order_info = live_engine.execute_signal(signal, decision.max_position_usdt, signal.current_price)
                    if order_info:
                        # ذخیره در دیتابیس
                        trade_id = repo.save_trade({
                            "symbol": symbol,
                            "side": signal.action.value.lower(),
                            "amount": order_info.get("amount", 0),
                            "entry_price": signal.current_price,
                            "size_usdt": decision.max_position_usdt,
                            "status": "open",
                            "order_id": order_info.get("order_id", ""),
                            "notes": signal.reason,
                        })
                        notifier.send_message(
                            f"✅ سفارش واقعی ثبت شد: {symbol} {signal.action.value} "
                            f"{decision.max_position_usdt:.2f} USDT @ {signal.current_price:.2f}"
                        )
                        notifier.send_message(
                            f"🆔 شناسه سفارش: {order_info.get('order_id', 'unknown')}"
                        )
                        log.info(f"✅ Order placed: {symbol} - order_id={order_info.get('order_id')}")
                    else:
                        notifier.send_error(f"❌ ثبت سفارش {symbol} ناموفق بود")
                else:
                    msg = f"{symbol}: موتور LIVE در دسترس نیست"
                    log.error(msg)
                    notifier.send_error(msg)

            else:  # OBSERVE
                log.info(f"👀 {symbol}: حالت OBSERVE - فقط لاگ، بدون اجرای معامله")
            # =======================================

        except Exception as e:
            log.exception(f"🔥 خطا در پردازش {symbol}: {e}")
            notifier.send_error(f"{symbol}: {e}")


def intelligence_loop(intelligence, notifier, portfolio_mgr, interval_seconds):
    """حلقه‌ی دوره‌ای تحلیل هوشمند بازار و ارسال گزارش/فرصت‌ها به تلگرام."""
    while True:
        try:
            snapshot = portfolio_mgr.fetch_snapshot()
            portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}

            report = intelligence.analyze(portfolio)

            # گزارش خلاصه
            notifier.send_intelligence_report(report["summary"])

            # فرصت‌های شناسایی‌شده
            for opp in report.get("opportunities", []):
                notifier.send_opportunity(
                    f"💎 {opp['symbol']}: {opp['action']} @ {opp['price']:,.2f} - {opp['reason']}"
                )

            time.sleep(interval_seconds)

        except Exception as e:
            log.exception(f"❌ خطا در حلقه‌ی هوش بازار: {e}")
            notifier.send_error(f"Intelligence loop error: {e}")
            time.sleep(60)  # در صورت خطا، ۱ دقیقه صبر کن


def chat_loop(telegram, client, discovery, advisor, portfolio_mgr):
    """حلقه‌ی دریافت و پاسخ به پیام‌های تلگرام"""
    engine = MarketIntelligence(settings)
    watchlist = [a.strip().upper() for a in settings.watchlist if isinstance(settings.watchlist, list)]
    handler = ChatHandler(
        client=client,
        discovery=discovery,
        engine=engine,
        watchlist=watchlist,
        advisor=advisor,
        portfolio_mgr=portfolio_mgr,
    )
    offset = None
    log.info("🤖 Telegram chat handler started")

    while True:
        try:
            updates = telegram.get_updates(offset=offset, timeout=25)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("edited_message")
                if not message or "text" not in message:
                    continue
                chat_id = str(message["chat"]["id"])
                text = message["text"]
                log.info(f"💬 Chat message from {chat_id}: {text}")
                reply = handler.handle(chat_id, text)
                telegram.send_to(chat_id, reply)
            if not updates:
                time.sleep(1)
        except Exception as e:
            log.exception(f"❌ خطا در حلقه‌ی چت: {e}")
            time.sleep(5)


# ===== تابع جدید: حلقه‌ی خودکار ارسال فرصت‌ها =====
def opportunity_loop(notifier, interval_seconds: int):
    """حلقه‌ی دوره‌ای برای بررسی و ارسال خودکار فرصت‌های معاملاتی"""
    forecast = ForecastReport()
    last_opportunities = {}

    while True:
        try:
            log.info("🔍 Checking for new opportunities...")
            opportunities = forecast.get_top_opportunities(
                symbols=["BTC", "ETH", "GOLD", "USD", "BNB", "ADA", "SHIB", "DOGE", "SOL"]
            )

            if opportunities:
                lines = ["💎 **فرصت‌های جدید شناسایی‌شده:**"]
                new_opps = []

                for opp in opportunities:
                    symbol = opp["symbol"]
                    recommendation = opp["recommendation"]
                    change = opp.get("change_percent", 0)
                    confidence = opp.get("confidence", "متوسط")

                    # اگر این فرصت قبلاً ارسال نشده یا تغییر کرده، ارسال کن
                    if symbol not in last_opportunities or last_opportunities[symbol] != recommendation:
                        new_opps.append(
                            f"- {symbol}: {recommendation} ({change:+.2f}%) - اطمینان: {confidence}"
                        )
                        last_opportunities[symbol] = recommendation

                if new_opps:
                    lines.extend(new_opps)
                    message = "\n".join(lines)
                    notifier.send_opportunity(message)
                    log.info(f"✅ Sent {len(new_opps)} new opportunities")
                else:
                    log.info("No new opportunities to send")
            else:
                log.info("No opportunities available")

        except Exception as e:
            log.exception(f"❌ Error in opportunity loop: {e}")
            notifier.send_error(f"Opportunity loop error: {e}")

        time.sleep(interval_seconds)
# =================================================


def main():
    from app.monitoring.logger import setup_logging
    setup_logging()
    start_health_server_in_background()

    # اعتبارسنجی تنظیمات
    errors = settings.validate()
    if errors:
        for e in errors:
            log.error(f"❌ Config error: {e}")
        return

    log.info(f"🚀 AI Trading Bot in {settings.mode} mode")
    log.info(f"📋 Watchlist: {settings.watchlist}")
    log.info(f"🧠 AI: {'Enabled' if settings.ai_enabled else 'Disabled'}")

    # ---------- راه‌اندازی سرویس‌ها ----------
    client = BitpinClient(
        settings.bitpin_base_url,
        settings.bitpin_api_key,
        settings.bitpin_api_secret,
    )
    portfolio_mgr = PortfolioManager(client)
    discovery = MarketDiscovery(client)
    market_data_mgr = MarketDataManager(settings)

    strategy = InitialStrategy()
    risk_mgr = RiskManager(settings)
    repo = Repository(settings.database_path)

    paper_engine = PaperTradingEngine()
    live_engine = LiveExecutionEngine(client) if settings.mode == "LIVE" else None

    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    notifier = BroadcastNotifier(telegram)

    # ---------- AI Advisor با ابزارهای واقعی ----------
    advisor = AIAdvisor(
        settings=settings,
        market_data_manager=market_data_mgr,
        portfolio_manager=portfolio_mgr,
        bitpin_client=client,
        repository=repo,  # برای یادگیری
    )
    log.info("🧠 AI Advisor initialized")

    # ---------- Market Intelligence ----------
    intelligence = MarketIntelligence(
        settings=settings,
        portfolio_manager=portfolio_mgr,
        bitpin_client=client,
    )
    threading.Thread(
        target=intelligence_loop,
        args=(intelligence, notifier, portfolio_mgr, settings.intelligence_interval_seconds),
        daemon=True,
    ).start()
    log.info("📊 Market Intelligence started")

    # ---------- Telegram Chat ----------
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

    # ===== شروع حلقه‌ی خودکار فرصت‌ها (جدید) =====
    threading.Thread(
        target=opportunity_loop,
        args=(notifier, settings.opportunity_check_interval),
        daemon=True,
        name="opportunity-loop",
    ).start()
    log.info(f"💎 Opportunity loop started (every {settings.opportunity_check_interval}s)")

    # ---------- هشدار LIVE ----------
    if settings.mode == "LIVE" and live_engine is not None:
        log.warning("🔴 LIVE MODE IS ACTIVE! Real orders will be placed.")
        notifier.send_message("⚠️ ربات در حالت LIVE فعال شد. سفارشات واقعی ثبت خواهند شد.")

    log.info("✅ Bot started successfully")

    # ---------- حلقه‌ی اصلی ----------
    while True:
        try:
            run_cycle(
                client=client,
                portfolio_mgr=portfolio_mgr,
                discovery=discovery,
                strategy=strategy,
                risk_mgr=risk_mgr,
                repo=repo,
                notifier=notifier,
                paper_engine=paper_engine,
                live_engine=live_engine,
                advisor=advisor,
            )
        except Exception as e:
            log.exception(f"🔥 Unhandled error in main loop: {e}")
            notifier.send_error(f"ربات با خطا مواجه شد: {e}")
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
