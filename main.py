"""
ربات تریدینگ هوشمند - نسخه نهایی با AI Agent خودمختار
قابلیت‌ها: یادگیری فعال، برنامه‌ریزی پویا، مدیریت ریسک تطبیقی
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict

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
from app.strategies.base import Action, Signal
from app.chat.handler import ChatHandler
from app.forecast.report import ForecastReport

# ===== قابلیت‌های جدید Agent خودمختار =====
from app.learning.active_learner import ActiveLearner, TradeResult
from app.planning.dynamic_planner import DynamicPlanner
from app.risk.adaptive_risk import AdaptiveRiskManager
# ===========================================

log = logging.getLogger("main")

# ===== جلوگیری از اسپم سیگنال‌ها =====
_last_signal = {}
_last_signal_time = {}
SIGNAL_COOLDOWN = getattr(settings, 'signal_cooldown_seconds', 1800)  # ۳۰ دقیقه

# ===== اصلاح مصرف توکن: کنترل تعداد فراخوانی واقعی AI در حلقه‌ی پس‌زمینه =====
# فقط زمان/قیمتِ «آخرین تصمیم واقعی AI» به ازای هر نماد نگه داشته می‌شود؛
# منطق معامله/ریسک/کیف‌پول کاملاً دست‌نخورده می‌ماند - این فقط تعیین می‌کند
# که آیا advisor.decide() این چرخه صدا زده شود یا نه.
_last_ai_decision: Dict[str, Dict[str, float]] = {}


def _should_call_ai_decision(symbol: str, current_price: float) -> bool:
    """
    True یعنی واقعاً وقت صدا زدن AI (مدل سنگین) برای این نماد است:
    یا حداقل ai_decision_min_interval_seconds گذشته، یا قیمت به‌اندازه‌ی کافی
    (ai_price_change_percent) از آخرین بررسی تغییر کرده. در غیر این صورت
    False برمی‌گردد و نماد در این چرخه WAIT در نظر گرفته می‌شود - بدون هیچ
    فراخوانی LLM (دقیقاً همان رفتار امن فعلی برای «AI در دسترس نیست»).
    """
    now = time.time()
    state = _last_ai_decision.get(symbol)
    min_interval = getattr(settings, "ai_decision_min_interval_seconds", 300)
    change_threshold = getattr(settings, "ai_price_change_percent", 1.0)

    if state is None:
        _last_ai_decision[symbol] = {"time": now, "price": current_price or 0.0}
        return True

    if (now - state["time"]) >= min_interval:
        _last_ai_decision[symbol] = {"time": now, "price": current_price or 0.0}
        return True

    if current_price and state.get("price"):
        change_pct = abs(current_price - state["price"]) / state["price"] * 100
        if change_pct >= change_threshold:
            _last_ai_decision[symbol] = {"time": now, "price": current_price}
            return True

    return False


def should_send_signal(symbol: str, new_action: str) -> bool:
    """بررسی آیا سیگنال جدید باید ارسال شود"""
    now = time.time()
    last_action = _last_signal.get(symbol)
    last_time = _last_signal_time.get(symbol, 0)

    if new_action != last_action or (now - last_time) > SIGNAL_COOLDOWN:
        _last_signal[symbol] = new_action
        _last_signal_time[symbol] = now
        return True
    return False


def check_and_close_positions(
    paper_engine,
    portfolio_mgr,
    repo,
    notifier,
    learner,
    client,
    current_prices: dict
):
    """
    بررسی و بستن موقعیت‌های باز در PAPER mode بر اساس حد سود/ضرر
    """
    positions = paper_engine.get_positions()
    if not positions:
        return

    for position in positions:
        symbol = position["symbol"]
        entry_price = position["entry_price"]
        side = position["side"]
        size_usdt = position["size_usdt"]

        # دریافت قیمت فعلی
        current_price = current_prices.get(symbol)
        if not current_price:
            continue

        # محاسبه سود/زیان
        if side == "BUY":
            profit_percent = ((current_price - entry_price) / entry_price) * 100
        else:  # SELL
            profit_percent = ((entry_price - current_price) / entry_price) * 100

        # حد ضرر و سود از پوزیشن
        stop_loss = position.get("stop_loss", entry_price * 0.92)
        take_profit = position.get("take_profit", entry_price * 1.15)

        should_close = False
        reason = ""

        if side == "BUY":
            if current_price <= stop_loss:
                should_close = True
                reason = f"Stop loss triggered: {current_price:.2f} <= {stop_loss:.2f}"
            elif current_price >= take_profit:
                should_close = True
                reason = f"Take profit triggered: {current_price:.2f} >= {take_profit:.2f}"
        else:  # SELL
            if current_price >= stop_loss:
                should_close = True
                reason = f"Stop loss triggered: {current_price:.2f} >= {stop_loss:.2f}"
            elif current_price <= take_profit:
                should_close = True
                reason = f"Take profit triggered: {current_price:.2f} <= {take_profit:.2f}"

        if should_close:
            # بستن معامله
            paper_engine.close_position(position, current_price)
            notifier.send_paper_trade(
                f"🔒 Closed {symbol} {side} @ {current_price:.2f} | Profit: {profit_percent:+.2f}% | Reason: {reason}"
            )

            # ===== ثبت یادگیری =====
            if learner is not None and settings.active_learning_enabled:
                try:
                    trade_result = learner.close_trade(symbol, current_price)
                    if trade_result:
                        notifier.send_message(
                            f"🧠 Trade recorded for learning: {symbol} → {trade_result.profit_percent:+.2f}%"
                        )
                        log.info(f"🧠 Learning recorded: {symbol} profit {trade_result.profit_percent:.2f}%")
                except Exception as e:
                    log.warning(f"Learning record error on close: {e}")


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
    learner=None,
    planner=None,
):
    """
    یک چرخه‌ی کامل با تصمیم‌گیری AI و مدیریت ریسک تطبیقی
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

    # ===== دریافت قیمت‌های فعلی برای بستن موقعیت‌ها =====
    current_prices = {}
    for asset, symbol in markets.items():
        try:
            ticker = client.get_ticker(symbol)
            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
            current_prices[symbol] = float(ticker.get("price", 0))
        except:
            pass

    # ===== بستن موقعیت‌های PAPER =====
    if settings.mode == "PAPER":
        check_and_close_positions(
            paper_engine=paper_engine,
            portfolio_mgr=portfolio_mgr,
            repo=repo,
            notifier=notifier,
            learner=learner,
            client=client,
            current_prices=current_prices,
        )

    for asset, symbol in markets.items():
        try:
            # ===== ۱. برنامه‌ریزی پویا (در صورت فعال بودن) =====
            if planner is not None and settings.dynamic_planning_enabled:
                adjusted_plan = planner.adjust_plan(symbol)
                if adjusted_plan:
                    log.info(f"📋 Plan adjusted for {symbol}: {adjusted_plan.action} @ {adjusted_plan.entry_price:.2f}")

                if symbol not in planner.get_active_plans():
                    new_plan = planner.generate_trading_plan(symbol)
                    if new_plan:
                        log.info(f"📋 New plan for {symbol}: {new_plan.action} @ {new_plan.entry_price:.2f}")
            # ==================================================

            # ===== ۲. تصمیم‌گیری =====
            if advisor is not None and settings.ai_enabled:
                current_price_for_gate = current_prices.get(symbol)
                if _should_call_ai_decision(symbol, current_price_for_gate):
                    signal = advisor.decide(asset, symbol)
                    log.info(f"🤖 AI decision for {symbol}: {signal.action.value} - {signal.reason}")
                else:
                    # ===== اصلاح مصرف توکن =====
                    # قیمت به‌اندازه‌ی کافی تغییر نکرده و زمان کافی هم از
                    # آخرین تصمیم واقعی AI نگذشته - صدا زدن دوباره‌ی مدل
                    # سنگین (gpt-oss-120b) با کل tool-calling برای این نماد
                    # در این چرخه فقط توکن هدر می‌دهد، بدون اینکه اطلاعات
                    # جدیدی وجود داشته باشد. مثل حالت «AI در دسترس نیست»،
                    # همین چرخه WAIT در نظر گرفته می‌شود - هیچ معامله‌ای جعل
                    # نمی‌شود و در چرخه‌ی بعدی که شرط بالا برقرار شود، AI
                    # دوباره واقعاً صدا زده خواهد شد.
                    signal = Signal(
                        market=symbol, action=Action.WAIT,
                        reason="صرفه‌جویی توکن: قیمت تغییر معناداری نکرده، تصمیم AI هنوز معتبر است",
                        current_price=current_price_for_gate or 0.0,
                    )
                    log.debug(f"⏭️ [TOKEN-SAVE] AI call skipped for {symbol} this cycle (no significant change)")
            else:
                # Fallback به استراتژی قانون‌محور
                ticker = client.get_ticker(symbol)
                if isinstance(ticker, list):
                    ticker = next((t for t in ticker if t.get("symbol") == symbol), {})
                elif not isinstance(ticker, dict):
                    ticker = {}
                signal = strategy.evaluate({"symbol": symbol, "ticker": ticker})
                log.info(f"📊 Fallback strategy for {symbol}: {signal.action.value} - {signal.reason}")
            # ==================================================

            repo.log_signal(signal)

            if signal.action in (Action.WAIT, Action.DO_NOT_TRADE):
                continue

            # ===== ۳. اعمال پلن (در صورت وجود) =====
            plan_info = None
            if planner is not None and settings.dynamic_planning_enabled:
                plan_info = planner.apply_plan_to_signal(symbol, signal)
                if plan_info:
                    # تنظیم سیگنال با مقادیر پلن
                    signal.stop_loss = plan_info.get("stop_loss", signal.stop_loss)
                    signal.take_profit = plan_info.get("take_profit", signal.take_profit)
                    signal.entry_price = plan_info.get("entry_price", signal.entry_price)
                    log.info(f"📋 Applied plan to {symbol}: SL={signal.stop_loss:.2f}, TP={signal.take_profit:.2f}")
            # ===================================================

            # ===== ۴. محاسبه مواجهه و تأیید ریسک =====
            if isinstance(risk_mgr, AdaptiveRiskManager):
                decision = risk_mgr.approve(
                    signal=signal,
                    portfolio_value_usdt=snapshot.total_value_usdt,
                    available_usdt=snapshot.available_usdt,
                    current_asset_exposure_usdt=snapshot.exposure_by_asset().get(asset, 0.0) * signal.current_price,
                    total_exposure_usdt=max(snapshot.total_value_usdt - snapshot.available_usdt, 0.0),
                    orderbook_liquidity_usdt=settings.min_liquidity,
                    estimated_slippage_percent=signal.slippage_percent,
                    price_age_seconds=0.0,
                )
            else:
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
            # ====================================================

            if not decision.approved:
                repo.log_risk_event("REJECTED", f"{symbol}: {decision.reason}")
                log.info(f"⛔ سیگنال {symbol} توسط ریسک‌منیجر رد شد: {decision.reason}")
                notifier.send_do_not_trade(f"{symbol}: {decision.reason}")
                continue

            # ===== ۵. ارسال سیگنال (با جلوگیری از اسپم) =====
            if should_send_signal(symbol, signal.action.value):
                notifier.send_signal(
                    f"{symbol} {signal.action.value} @ {signal.current_price:,.2f} - {signal.reason}"
                )
            else:
                log.debug(f"Skipping duplicate signal for {symbol} (cooldown)")
            # ==================================================

            # ===== ۶. نظر AI مکمل =====
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
            # ==========================

            # ===== ۷. اجرای معامله =====
            position = None
            trade_result = None

            if settings.mode == "PAPER":
                position = paper_engine.open_position(
                    signal,
                    decision.max_position_usdt,
                    signal.current_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
                if position:
                    notifier.send_paper_trade(
                        f"📄 {symbol} {signal.action.value} حجم={decision.max_position_usdt:.2f} USDT @ {signal.current_price:,.2f} | SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f}"
                    )
                    trade_result = {
                        "symbol": symbol,
                        "action": signal.action.value,
                        "entry_price": signal.current_price,
                        "size_usdt": decision.max_position_usdt,
                        "status": "open",
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                    }
                else:
                    notifier.send_error(f"❌ PAPER trade failed for {symbol}")

            elif settings.mode == "LIVE":
                if live_engine is not None:
                    order_info = live_engine.execute_signal(signal, decision.max_position_usdt, signal.current_price)
                    if order_info:
                        trade_id = repo.save_trade({
                            "symbol": symbol,
                            "side": signal.action.value.lower(),
                            "amount": order_info.get("amount", 0),
                            "entry_price": signal.current_price,
                            "size_usdt": decision.max_position_usdt,
                            "status": "open",
                            "order_id": order_info.get("order_id", ""),
                            "notes": signal.reason,
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                        })
                        notifier.send_message(
                            f"✅ سفارش واقعی ثبت شد: {symbol} {signal.action.value} "
                            f"{decision.max_position_usdt:.2f} USDT @ {signal.current_price:.2f} | SL={signal.stop_loss:.2f} TP={signal.take_profit:.2f}"
                        )
                        notifier.send_message(f"🆔 شناسه سفارش: {order_info.get('order_id', 'unknown')}")
                        position = order_info
                        trade_result = {
                            "symbol": symbol,
                            "action": signal.action.value,
                            "entry_price": signal.current_price,
                            "order_id": order_info.get("order_id"),
                            "status": "open",
                            "stop_loss": signal.stop_loss,
                            "take_profit": signal.take_profit,
                        }
                    else:
                        notifier.send_error(f"❌ ثبت سفارش {symbol} ناموفق بود")
                else:
                    notifier.send_error(f"{symbol}: موتور LIVE در دسترس نیست")

            else:  # OBSERVE
                log.info(f"👀 {symbol}: حالت OBSERVE - فقط لاگ، بدون اجرای معامله")
            # ==================================================

            # ===== ۸. ثبت یادگیری اولیه (در صورت فعال بودن) =====
            if settings.active_learning_enabled and learner is not None and trade_result:
                try:
                    # ثبت معامله برای یادگیری (با وضعیت open)
                    # بسته شدن در حلقه‌ی check_and_close_positions انجام می‌شود
                    log.info(f"🧠 Trade recorded for learning: {symbol} (open)")
                except Exception as e:
                    log.warning(f"Learning record error: {e}")
            # ==================================================

        except Exception as e:
            log.exception(f"🔥 خطا در پردازش {symbol}: {e}")
            notifier.send_error(f"{symbol}: {e}")


def intelligence_loop(intelligence, notifier, portfolio_mgr, interval_seconds):
    while True:
        try:
            snapshot = portfolio_mgr.fetch_snapshot()
            portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}
            report = intelligence.analyze(portfolio, snapshot=snapshot)
            notifier.send_intelligence_report(report["summary"])
            for opp in report.get("opportunities", []):
                notifier.send_opportunity(
                    f"💎 {opp['symbol']}: {opp['action']} @ {opp['price']:,.2f} - {opp['reason']}"
                )
            time.sleep(interval_seconds)
        except Exception as e:
            log.exception(f"❌ خطا در حلقه‌ی هوش بازار: {e}")
            notifier.send_error(f"Intelligence loop error: {e}")
            time.sleep(60)


def chat_loop(telegram, client, discovery, advisor, portfolio_mgr, risk_mgr=None):
    engine = MarketIntelligence(settings)
    watchlist = [a.strip().upper() for a in settings.watchlist if isinstance(settings.watchlist, list)]
    handler = ChatHandler(
        client=client,
        discovery=discovery,
        engine=engine,
        watchlist=watchlist,
        advisor=advisor,
        portfolio_mgr=portfolio_mgr,
        # ===== اصلاح ریشه‌ای: قبلاً risk_mgr اصلاً به chat_loop/ChatHandler
        # پاس داده نمی‌شد، پس پاسخ‌های چت (مثل «چی بخرم؟») هیچ‌وقت از آخرین
        # Guardrail عبور نمی‌کردند - این خط همان risk_mgr واقعی حلقه‌ی اصلی
        # معاملات را به مسیر چت هم وصل می‌کند. =====
        risk_mgr=risk_mgr,
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
                try:
                    reply = handler.handle(chat_id, text)
                except Exception as e:
                    # ===== اصلاح: حتی اگر ChatHandler هم خطای غیرمنتظره بدهد،
                    # کاربر باید حداقل یک پیام خطا ببیند، نه سکوت کامل =====
                    log.exception(f"❌ خطای پردازش پیام چت: {e}")
                    reply = f"❌ خطا در پردازش پیام: {e}"
                telegram.send_to(chat_id, reply)
            if not updates:
                time.sleep(1)
        except Exception as e:
            log.exception(f"❌ خطا در حلقه‌ی چت: {e}")
            time.sleep(5)


def opportunity_loop(notifier, interval_seconds: int):
    forecast = ForecastReport()
    last_opportunities = {}
    last_sent_time = {}

    # ===== اصلاح: فقط فرصت‌های واقعاً خوب اطلاع داده بشن =====
    MIN_CHANGE_PERCENT = 3.0
    MIN_CONFIDENCE = "بالا"
    OPPORTUNITY_COOLDOWN = 3600

    while True:
        try:
            log.info("🔍 Checking for new opportunities...")
            opportunities = forecast.get_top_opportunities(
                symbols=["BTC", "ETH", "GOLD", "USD", "BNB", "ADA", "SHIB", "DOGE", "SOL"]
            )
            if opportunities:
                new_opps = []
                now = time.time()
                for opp in opportunities:
                    symbol = opp["symbol"]
                    recommendation = opp["recommendation"]
                    change = opp.get("change_percent", 0)
                    confidence = opp.get("confidence", "متوسط")

                    if abs(change) < MIN_CHANGE_PERCENT or confidence != MIN_CONFIDENCE:
                        continue

                    same_as_last = last_opportunities.get(symbol) == recommendation
                    recently_sent = (now - last_sent_time.get(symbol, 0)) < OPPORTUNITY_COOLDOWN
                    if same_as_last and recently_sent:
                        continue

                    new_opps.append(
                        f"- {symbol}: {recommendation} ({change:+.2f}%) - اطمینان: {confidence}"
                    )
                    last_opportunities[symbol] = recommendation
                    last_sent_time[symbol] = now

                if new_opps:
                    lines = ["💎 **فرصت‌های جدید شناسایی‌شده:**"] + new_opps
                    notifier.send_opportunity("\n".join(lines))
                    log.info(f"✅ Sent {len(new_opps)} new opportunities")
            time.sleep(interval_seconds)
        except Exception as e:
            log.exception(f"❌ Error in opportunity loop: {e}")
            notifier.send_error(f"Opportunity loop error: {e}")
            time.sleep(60)


def main():
    from app.monitoring.logger import setup_logging
    setup_logging()
    start_health_server_in_background()

    errors = settings.validate()
    if errors:
        for e in errors:
            log.error(f"❌ Config error: {e}")
        return

    log.info(f"🚀 AI Trading Bot in {settings.mode} mode")
    log.info(f"📋 Watchlist: {settings.watchlist}")
    log.info(f"🧠 AI: {'Enabled' if settings.ai_enabled else 'Disabled'}")

    # ---------- راه‌اندازی سرویس‌های اصلی ----------
    client = BitpinClient(settings.bitpin_base_url, settings.bitpin_api_key, settings.bitpin_api_secret)
    portfolio_mgr = PortfolioManager(client)
    discovery = MarketDiscovery(client)
    market_data_mgr = MarketDataManager(settings)

    strategy = InitialStrategy()
    repo = Repository(settings.database_path)

    paper_engine = PaperTradingEngine()
    live_engine = LiveExecutionEngine(client) if settings.mode == "LIVE" else None

    telegram = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    notifier = BroadcastNotifier(telegram)

    # ---------- AI Advisor ----------
    advisor = AIAdvisor(
        settings=settings,
        market_data_manager=market_data_mgr,
        portfolio_manager=portfolio_mgr,
        bitpin_client=client,
        repository=repo,
    )
    log.info("🧠 AI Advisor initialized")

    # ===== قابلیت‌های جدید Agent خودمختار =====

    # ۱. یادگیری فعال
    learner = None
    if settings.active_learning_enabled:
        learner = ActiveLearner(repo, settings)
        log.info("🧠 Active learning enabled")

    # ۲. برنامه‌ریزی پویا
    planner = None
    if settings.dynamic_planning_enabled:
        planner = DynamicPlanner(settings, market_data_mgr, portfolio_mgr, advisor)
        log.info("📋 Dynamic planning enabled")

    # ۳. مدیریت ریسک تطبیقی
    if settings.adaptive_risk_enabled:
        risk_mgr = AdaptiveRiskManager(settings, repo, market_data_mgr)
        log.info("🛡️ Adaptive risk management enabled")
    else:
        risk_mgr = RiskManager(settings)
        log.info("📊 Using standard risk manager")
    # ==========================================

    # ---------- Market Intelligence ----------
    intelligence = MarketIntelligence(settings, portfolio_manager=portfolio_mgr, bitpin_client=client)
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
            args=(telegram, client, discovery, advisor, portfolio_mgr, risk_mgr),
            daemon=True,
            name="telegram-chat",
        ).start()
        log.info("📨 Telegram chat handler started")

    # ---------- فرصت‌های خودکار ----------
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

    # ---------- خلاصه قابلیت‌های فعال ----------
    log.info("✅ Bot started successfully")
    log.info(f"📊 Features: AI={settings.ai_enabled}, ActiveLearning={settings.active_learning_enabled}, "
             f"DynamicPlanning={settings.dynamic_planning_enabled}, "
             f"AdaptiveRisk={settings.adaptive_risk_enabled}")

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
                learner=learner,
                planner=planner,
            )
        except Exception as e:
            log.exception(f"🔥 Unhandled error in main loop: {e}")
            notifier.send_error(f"ربات با خطا مواجه شد: {e}")
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
