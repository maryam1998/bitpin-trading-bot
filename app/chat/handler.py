import logging
import json
from typing import Dict, Any, List, Optional
from app.strategies.base import Action, Signal
from app.bitpin.pricing import MarketSymbolResolver, extract_ticker_price, find_ticker
from app.bitpin.auth import BitpinAuthError

log = logging.getLogger(__name__)

# ===== اصلاح ریشه‌ای: عبارت‌های شناسایی درخواست «الان چی بخرم؟» =====
# قبلاً هیچ مسیر تشخیصی برای این سوال وجود نداشت؛ چنین پیامی به _get_ai_response
# می‌رفت که (۱) از یک پرامپت تقریباً خالی استفاده می‌کرد، (۲) قیمت‌ها را با
# نماد اشتباه (مثلاً "BTC" به‌جای "BTC_USDT") می‌گرفت که همیشه شکست می‌خورد،
# و (۳) هیچ اتصالی به RiskManager نداشت. حالا این عبارت‌ها به یک Flow صریح و
# قابل‌ردیابی (Portfolio→Market→News→Opportunity→AI→Risk→Response) می‌روند.
_BUY_INTENT_PHRASES = [
    "چی بخرم", "چی بخریم", "چه بخرم", "چه چیزی بخرم", "چی رو بخرم", "چیو بخرم",
    "چی خوبه بخرم", "پیشنهاد خرید", "بهترین خرید", "چی بگیرم", "چی معامله کنم",
]


class ChatHandler:
    def __init__(self, client, discovery, engine, watchlist, max_assets=10, advisor=None,
                 portfolio_mgr=None, risk_mgr=None):
        self.client = client
        self.discovery = discovery
        self.engine = engine
        self.watchlist = watchlist
        self.max_assets = max_assets
        self.advisor = advisor
        self.portfolio_mgr = portfolio_mgr
        # ===== اصلاح ریشه‌ای: قبلاً RiskManager اصلاً به ChatHandler پاس داده
        # نمی‌شد، یعنی پاسخ «چی بخرم؟» هیچ‌وقت از آخرین Guardrail عبور نمی‌کرد. =====
        self.risk_mgr = risk_mgr
        # ===== اصلاح: تشخیص نماد دقیق بازار از لیست واقعی بازارها، به‌جای
        # حدس مستقیم "{asset}_USDT" - رفع مشکل قیمت SHIB =====
        self._symbol_resolver = MarketSymbolResolver(client)

    def handle(self, chat_id: str, text: str) -> str:
        # ===== اصلاح: هیچ‌وقت نباید پاسخ کاملاً خالی/بی‌صدا برگردد =====
        # قبلاً اگر یک خطای پیش‌بینی‌نشده در مسیر تشخیص دستور رخ می‌داد،
        # کل حلقه‌ی چت متوقف می‌شد و کاربر هیچ پاسخی نمی‌گرفت (حتی پیام خطا).
        try:
            return self._route(text)
        except Exception as e:
            log.exception(f"خطای پیش‌بینی‌نشده در پردازش پیام: {text!r}")
            return f"❌ یک خطای غیرمنتظره رخ داد: {e}"

    def _route(self, text: str) -> str:
        text = text.strip().lower()
        if text in ["/start", "سلام", "hi"]:
            return "👋 سلام! من ربات تریدینگ هوشمند هستم.\nبرای مشاهده راهنما، /help را بفرستید."

        elif text == "/help":
            return (
                "📚 **راهنمای ربات:**\n"
                "/portfolio یا موجودی یا کیف پول - نمایش وضعیت کیف پول\n"
                "/signal BTC - دریافت سیگنال برای بیت‌کوین\n"
                "/analysis - تحلیل کلی بازار\n"
                "/forecast BTC - پیش‌بینی قیمت ۳۰ روز آینده\n"
                "/forecast GOLD - پیش‌بینی قیمت طلا\n"
                "/opportunities - نمایش بهترین فرصت‌های امروز\n"
                "سوالات خود را به زبان فارسی بپرسید."
            )

        # ===== اصلاح: «موجودی» و چند نام رایج دیگر هم باید کیف پول را نشان بدهند =====
        # قبلاً فقط دقیقاً "/portfolio" یا "کیف پول" شناسایی می‌شد و هر چیز دیگری
        # (مثل "موجودی") به مسیر چت آزاد با AI می‌رفت که نتیجه‌ی گیج‌کننده می‌داد.
        elif text in ["/portfolio", "/wallet", "/balance", "کیف پول", "موجودی", "موجودیم", "wallet", "balance"]:
            return self._get_portfolio_info()

        elif text.startswith("/signal"):
            parts = text.split()
            if len(parts) > 1:
                return self._get_signal(parts[1].upper())
            return "لطفاً یک ارز را مشخص کنید، مثلاً: /signal BTC"

        elif text.startswith("/forecast"):
            parts = text.split()
            symbol = parts[1].upper() if len(parts) > 1 else "BTC"
            try:
                from app.forecast.report import ForecastReport
                return ForecastReport().generate_text_report(symbol, days=30)
            except Exception as e:
                return f"❌ خطا در پیش‌بینی: {e}"

        elif text == "/opportunities":
            try:
                from app.forecast.report import ForecastReport
                ops = ForecastReport().get_top_opportunities()
                if not ops:
                    return "🔍 هیچ فرصتی یافت نشد."
                lines = ["💎 **بهترین فرصت‌ها:**"]
                for op in ops:
                    lines.append(f"- {op['symbol']}: {op['recommendation']} ({op['change_percent']:+.2f}%)")
                return "\n".join(lines)
            except Exception as e:
                return f"❌ خطا: {e}"

        elif "طلا" in text or "دلار" in text:
            return self._get_market_analysis(text)

        # ===== اصلاح ریشه‌ای: مسیر مشخص و ردیابی‌شونده برای «الان چی بخرم؟» =====
        elif any(phrase in text for phrase in _BUY_INTENT_PHRASES):
            return self._handle_buy_query(text)

        else:
            return self._get_ai_response(text)

    def _get_portfolio_info(self) -> str:
        """دریافت اطلاعات کیف پول با محاسبه‌ی available = balance - frozen"""
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            # اگر endpoint صفحه‌بندی‌شده پاسخ بدهد، به یک لیست ساده تبدیل می‌شود
            if isinstance(wallets, dict):
                wallets = wallets.get("results") or []
            if not wallets:
                return "❌ اطلاعات کیف پول در دسترس نیست."

            # دریافت قیمت USDT/IRT
            try:
                tickers = self.client.get_ticker("USDT_IRT")
                ticker = find_ticker(tickers, "USDT_IRT")
                usdt_irt_price = extract_ticker_price(ticker)
            except Exception:
                usdt_irt_price = 0.0

            if usdt_irt_price <= 0:
                return "❌ قیمت USDT/IRT در دسترس نیست."

            # ===== اصلاح اصلی مغایرت موجودی =====
            # فیلد "balance" فقط بخش آزاد/قابل‌برداشت است، نه کل دارایی؛
            # "frozen" مبلغی است که جدا از آن (مثلاً در یک سفارش باز) قفل
            # شده. نسخه‌ی قبلی "balance" را به‌تنهایی به‌عنوان «کل» در نظر
            # می‌گرفت، پس هر مبلغی که در سفارش باز قفل بود (در این حساب
            # ~۱۰۰ USDT) از گزارش کیف‌پول به‌طور کامل گم می‌شد؛ در حالی که
            # بیت‌پین آن را در «کل» لحاظ می‌کند. علاوه بر این، چند ردیف
            # احتمالی برای یک دارایی جمع (نه جایگزین) می‌شوند.
            balances = {}
            for item in wallets:
                asset = item.get("asset", "")
                free = float(item.get("balance", 0) or 0)
                frozen = float(item.get("frozen", 0) or 0)
                total = free + frozen
                if total <= 0:
                    continue
                log.info(f"💰 wallet raw: asset={asset} balance(free)={free} frozen={frozen} -> total={total}")
                if asset in balances:
                    balances[asset]["balance"] += total
                    balances[asset]["available"] += free
                else:
                    balances[asset] = {"balance": total, "available": free}
            # ========================================================

            total_irt = 0.0
            asset_values_irt = {}
            unpriced_assets = []  # قانون ۱۱: به‌جای فرض صفر، این‌ها را جدا اعلام می‌کنیم

            for asset, data in balances.items():
                balance = data["balance"]
                if asset == "IRT":
                    value_irt = balance
                elif asset == "USDT":
                    value_irt = balance * usdt_irt_price
                else:
                    value_irt = None
                    try:
                        # ===== اصلاح: نماد دقیق بازار از لیست واقعی بازارها
                        # گرفته می‌شود، نه با حدس مستقیم "{asset}_USDT" -
                        # همین حدس باعث می‌شد قیمت SHIB پیدا نشود، چون نماد
                        # واقعی بازار آن روی بیت‌پین لزوماً همین فرمت نیست. =====
                        usdt_symbol = self._symbol_resolver.resolve(asset, "USDT")
                        ticker_asset = self.client.get_ticker(usdt_symbol)
                        price_usdt = extract_ticker_price(find_ticker(ticker_asset, usdt_symbol))
                        if price_usdt > 0:
                            value_irt = balance * price_usdt * usdt_irt_price
                        else:
                            irt_symbol = self._symbol_resolver.resolve(asset, "IRT")
                            ticker_irt = self.client.get_ticker(irt_symbol)
                            price_irt = extract_ticker_price(find_ticker(ticker_irt, irt_symbol))
                            if price_irt > 0:
                                value_irt = balance * price_irt
                            else:
                                log.info(f"⚠️ قیمت معتبری برای {asset} پیدا نشد (نه {usdt_symbol}, نه {irt_symbol}).")
                    except Exception as e:
                        log.warning(f"خطا در قیمت‌گیری {asset}: {e}")
                        value_irt = None

                if value_irt is None:
                    unpriced_assets.append(asset)
                    continue

                asset_values_irt[asset] = value_irt
                total_irt += value_irt

            total_usdt = total_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0

            return self._format_advisor_report(balances, asset_values_irt, unpriced_assets, total_irt, total_usdt, usdt_irt_price)

        # ===== اصلاح: 429/rate-limit روی Login نباید کل گزارش پرتفولیو را
        # با یک exception خام (مثلاً "Login failed: 429 Request was
        # throttled...") از کار بیندازد؛ پیام واضح و غیرفنی نشان می‌دهیم و
        # کاربر را به تلاش دوباره‌ی کوتاه بعد راهنمایی می‌کنیم. منطق محاسبه‌ی
        # موجودی و خروجی موفق بالا کاملاً بدون تغییر مانده است.
        except BitpinAuthError as e:
            log.warning(f"Portfolio temporarily unavailable due to Bitpin login rate-limit: {e}")
            return "⏳ سرویس ورود بیت‌پین موقتاً محدود شده (rate limit). لطفاً چند لحظه‌ی دیگر دوباره امتحان کن."
        except Exception as e:
            log.error(f"Portfolio error: {e}")
            return f"❌ خطا در دریافت کیف پول: {e}"

    # ===== نمادها/ایموجی نمایش هر دارایی =====
    _ASSET_EMOJI = {
        "USDT": "💵", "IRT": "🇮🇷", "ETH": "💎", "BTC": "🟠", "XRP": "🟣",
        "TRX": "🔵", "SHIB": "🐕", "DOGE": "🐶", "BNB": "🟡", "ADA": "🔷",
        "SOL": "🟪", "DOT": "⚪", "LINK": "🔗",
    }
    _STABLE_ASSETS = {"USDT", "IRT"}

    @staticmethod
    def _format_balance(balance: float) -> str:
        """موجودی رو بدون صفرهای اضافی نشون می‌ده (بدون آسیب زدن به ارقام غیرصفر)"""
        if balance == int(balance):
            return f"{int(balance):,}"
        text = f"{balance:,.6f}".rstrip("0")
        return text.rstrip(".") if text.endswith(".") else text

    def _format_advisor_report(self, balances, asset_values_irt, unpriced_assets,
                                total_irt, total_usdt, usdt_irt_price) -> str:
        """
        گزارش کیف پول به سبک «مشاور مالی»: هر دارایی در یک بلوک جدا، مرتب‌شده
        بر اساس ارزش، به‌همراه یک تحلیل ساده و صادقانه از ترکیب پرتفولیو.
        هیچ عددی این‌جا حدس زده نمی‌شود - همه از asset_values_irt (محاسبه‌شده
        از قیمت واقعی بیت‌پین) می‌آیند.
        """
        lines = []
        lines.append("💰 **وضعیت سرمایه**")
        lines.append("━━━━━━━━━━━━")
        lines.append(f"ارزش کل: **{total_usdt:,.2f} USDT**")
        lines.append(f"معادل: **حدود {total_irt / 1_000_000:,.1f} میلیون تومان**")
        lines.append("")
        lines.append("📊 **دارایی‌های من**")
        lines.append("━━━━━━━━━━━━")

        sorted_assets = sorted(asset_values_irt.items(), key=lambda kv: kv[1], reverse=True)
        for asset, value_irt in sorted_assets:
            value_usdt = value_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0
            pct = (value_irt / total_irt * 100) if total_irt > 0 else 0.0
            emoji = self._ASSET_EMOJI.get(asset, "🔸")
            balance = balances[asset]["balance"]
            lines.append(f"\n{emoji} **{asset}**")
            lines.append(f"موجودی: {self._format_balance(balance)}")
            if value_usdt < 0.01:
                lines.append("ارزش: کمتر از 0.01 USDT")
                lines.append("سهم: ناچیز")
            else:
                lines.append(f"ارزش: ≈ {value_usdt:,.2f} USDT")
                lines.append(f"سهم: **{pct:.1f}٪**")

        if unpriced_assets:
            lines.append("\n⚠️ قیمت این دارایی‌ها الان در دسترس نبود (در جمع کل لحاظ نشده‌اند): " + "، ".join(unpriced_assets))

        # ===== تحلیل ترکیب پرتفولیو (کاملاً بر اساس اعداد واقعی بالا، بدون حدس بازار) =====
        cash_irt = sum(v for a, v in asset_values_irt.items() if a in self._STABLE_ASSETS)
        cash_ratio = (cash_irt / total_irt * 100) if total_irt > 0 else 0.0
        volatile = [(a, v) for a, v in asset_values_irt.items() if a not in self._STABLE_ASSETS]
        top_volatile = max(volatile, key=lambda kv: kv[1]) if volatile else None
        top_volatile_pct = (top_volatile[1] / total_irt * 100) if (top_volatile and total_irt > 0) else 0.0
        # بزرگ‌ترین دارایی به‌طور مطلق (شامل نقد) - این چیزیه که واقعاً باید
        # به عنوان «بیشترین تمرکز» گزارش بشه، نه فقط بزرگ‌ترین رمزارز
        largest_overall = max(asset_values_irt.items(), key=lambda kv: kv[1]) if asset_values_irt else None
        largest_overall_pct = (largest_overall[1] / total_irt * 100) if (largest_overall and total_irt > 0) else 0.0

        if cash_ratio >= 50:
            liquidity = "🟢 خوب"
        elif cash_ratio >= 20:
            liquidity = "🟡 متوسط"
        else:
            liquidity = "🔴 پایین"

        # ===== اصلاح: تنوع باید هم به تمرکز نقد و هم به تمرکز درون رمزارزها توجه کنه =====
        # قبلاً فقط بر اساس سهم بزرگ‌ترین رمزارز از کل پرتفولیو حساب می‌شد؛
        # وقتی اکثر پول نقده، این عدد ذاتاً کوچیک می‌موند و همیشه «خوب» می‌داد،
        # حتی وقتی ۸۵٪ سرمایه فقط توی ۲ نوع دارایی (USDT/IRT) بود.
        if cash_ratio >= 90 or top_volatile_pct >= 50:
            diversity = "🔴 پایین"
        elif cash_ratio >= 70 or top_volatile_pct >= 25:
            diversity = "🟡 متوسط"
        else:
            diversity = "🟢 خوب"

        volatile_ratio = 100 - cash_ratio
        if volatile_ratio >= 60:
            risk_level = "🔴 بالا"
        elif volatile_ratio >= 30:
            risk_level = "🟡 متوسط"
        else:
            risk_level = "🟢 پایین"

        # ===== اصلاح: نگاه به فرصت‌های واقعی بازار، نه فقط ترکیب پرتفولیو =====
        # قبلاً تصمیم فقط از روی درصد نقد بودن سرمایه گرفته می‌شد و همیشه
        # «صبر» می‌گفت، حتی اگه همون لحظه یه فرصت واقعی توی بازار بود.
        opportunities = []
        if self.engine is not None:
            try:
                portfolio_amounts = {a: balances[a]["balance"] for a in balances}
                opportunities = self.engine.get_opportunities(portfolio_amounts)
            except Exception as e:
                log.warning(f"opportunity check failed in portfolio report: {e}")

        buy_opps = [o for o in opportunities if o.get("action") == "BUY"]
        sell_opps = [o for o in opportunities if o.get("action") == "SELL" and o.get("symbol") in asset_values_irt]

        lines.append("\n━━━━━━━━━━━━")
        lines.append("🧠 **نظر مشاور**")
        advisor_notes = []
        if largest_overall:
            cash_note = f"بیشترین بخش سرمایه‌ات در {largest_overall[0]}"
            other_stable = [a for a in self._STABLE_ASSETS if a in asset_values_irt and a != largest_overall[0]]
            if largest_overall[0] in self._STABLE_ASSETS and other_stable:
                cash_note += f" و {other_stable[0]} قرار دارد و حدود {cash_ratio:.0f}٪ کل سرمایه را تشکیل می‌دهد."
            else:
                cash_note += f" قرار دارد و حدود {largest_overall_pct:.0f}٪ کل سرمایه را تشکیل می‌دهد."
            advisor_notes.append(cash_note)
        advisor_notes.append(f"سطح ریسک فعلی پرتفولیو: {risk_level.split()[-1]}.")
        lines.append(" ".join(advisor_notes))

        lines.append("\n🎯 **بهترین کار الان**")
        action, best_move = self._decide_best_move(cash_ratio, buy_opps, sell_opps, top_volatile, top_volatile_pct, balances)
        lines.append(f"{action}\n{best_move}")

        lines.append("\n⚠️ **ریسک پرتفولیو**")
        lines.append(f"{liquidity.split()[0]} نقدینگی: {liquidity.split()[1] if len(liquidity.split())>1 else ''}")
        lines.append(f"{diversity.split()[0]} تنوع: {' '.join(diversity.split()[1:])}")
        lines.append(f"{risk_level.split()[0]} ریسک فعلی: {risk_level.split()[1]}")

        lines.append("\n💡 این تحلیل ترکیب دو چیزه: دارایی‌های خودت و وضعیت لحظه‌ای بازار - نه فقط حدس. تضمینی در کار نیست، فقط راهنماییه.")


        return "\n".join(lines)

    def _decide_best_move(self, cash_ratio, buy_opps, sell_opps, top_volatile, top_volatile_pct, balances):
        """
        ===== اصلاح: «بهترین کار الان» دیگر یک Rule ثابت نیست =====
        قبلاً این تصمیم فقط از روی درصد نقد بودن سرمایه گرفته می‌شد و اگه
        نقد بالای ۵۰٪ بود، همیشه و بدون هیچ بررسی واقعی «🟡 صبر» برمی‌گشت.
        حالا اول از AIAdvisor.decide_best_action می‌خواهیم که با ابزارهای
        واقعی (Portfolio + Market Data + News + Opportunity Analysis) وضعیت
        لحظه‌ای بازار را بررسی و تصمیم واقعی بگیرد. اگه فرصت خرید معتبری پیدا
        بشه، AI می‌تواند 🟢 خرید را پیشنهاد بدهد؛ اگه واقعاً به صبر برسه، باید
        دلیل واقعی بنویسه.

        اعداد این متد (cash_ratio, top_volatile_pct, opportunities) همه از
        قبل در پایتون/API محاسبه شده‌اند و فقط به‌عنوان context به AI داده
        می‌شوند - AI عدد جدید نمی‌سازد.

        اگر AI در دسترس نبود یا نتوانست تصمیم معتبری بدهد، از همان منطق
        قانون‌محور قبلی (بر پایه‌ی فرصت‌های واقعی از API) به‌عنوان fallback
        استفاده می‌کنیم تا خروجی هیچ‌وقت خالی/خراب نشود.
        """
        if self.advisor is not None:
            try:
                context = {
                    "cash_ratio_percent": round(cash_ratio, 1),
                    "top_volatile_asset": top_volatile[0] if top_volatile else None,
                    "top_volatile_asset_percent_of_portfolio": round(top_volatile_pct, 1),
                    "buy_opportunities": buy_opps,
                    "sell_opportunities": sell_opps,
                    "held_assets": list(balances.keys()),
                }
                ai_result = self.advisor.decide_best_action(context)
            except Exception as e:
                log.warning(f"AI best-action decision failed, using fallback rules: {e}")
                ai_result = None

            if ai_result:
                action_display = {
                    "BUY": "🟢 بررسی خرید",
                    "SELL": "🔴 بررسی برداشت سود",
                    "REDUCE_CONCENTRATION": "🔴 کاهش تمرکز",
                    "WAIT": "🟡 صبر",
                }.get(ai_result["action"], "🟡 صبر")
                return action_display, ai_result["reason"]

        # ===== fallback قانون‌محور: فقط وقتی AI در دسترس نباشد یا خطا بدهد =====
        if buy_opps:
            opp = buy_opps[0]
            action = "🟢 بررسی خرید"
            best_move = (
                f"حدود {cash_ratio:.0f}٪ سرمایه‌ات در USDT و IRT است و هنوز وارد دارایی‌های پرنوسان نشده‌ای. "
                f"الان یک فرصت احتمالی روی {opp['symbol']} دیده می‌شه ({opp.get('reason', '')}). "
                f"چون بخش زیادی از سرمایه‌ات نقده، اگه خواستی وارد بشی، بهتره مرحله‌ای باشه، نه یکجا."
            )
        elif sell_opps:
            opp = sell_opps[0]
            action = "🔴 بررسی برداشت سود"
            best_move = f"{opp['symbol']} که داری، {opp.get('reason', '')}. شاید وقت خوبی باشه بخشی از سود رو ذخیره کنی."
        elif cash_ratio >= 50:
            action = "🟡 صبر"
            best_move = (
                f"حدود {cash_ratio:.0f}٪ سرمایه‌ات در USDT و IRT است و هنوز وارد دارایی‌های پرنوسان نشده‌ای؛ "
                "الان فرصت خرید به‌اندازه‌ای قوی در بازار دیده نمی‌شه که ورود فوری رو توجیه کنه، بهتره فعلاً "
                "صبر کنیم. اگه فرصت مناسبی پیش بیاد، بهت خبر می‌دم."
            )
        elif top_volatile_pct >= 50:
            action = "🔴 کاهش تمرکز"
            best_move = f"بیشتر سرمایه‌ت روی {top_volatile[0]} متمرکزه؛ بهتره برای کاهش ریسک بخشی از پرتفولیو رو متنوع‌تر کنی."
        else:
            action = "🟡 صبر"
            best_move = "ترکیب فعلی پرتفولیو نسبتاً متعادله؛ فعلاً نیازی به تغییر فوری نیست. اگه فرصت مناسبی پیش بیاد، بهت خبر می‌دم."
        return action, best_move

    def _get_signal(self, symbol: str) -> str:
        if not self.advisor:
            return "🤖 AI در دسترس نیست."

        try:
            asset = symbol.split('_')[0]
            signal = self.advisor.decide(asset, symbol)

            if signal.action == Action.WAIT:
                return f"📈 **سیگنال {symbol}**\nقیمت: {signal.current_price:,.2f}\nتوصیه: 🟡 WAIT\nدلیل: {signal.reason}"

            return (
                f"📈 **سیگنال {symbol}**\n"
                f"قیمت: {signal.current_price:,.2f}\n"
                f"توصیه: {'🟢 BUY' if signal.action == Action.BUY else '🔴 SELL'}\n"
                f"دلیل: {signal.reason}\n"
                f"ورود: {signal.entry_price:,.2f}\n"
                f"حد ضرر: {signal.stop_loss:,.2f}\n"
                f"حد سود: {signal.take_profit:,.2f}"
            )
        except Exception as e:
            return f"❌ خطا: {e}"

    def _get_market_analysis(self, text: str) -> str:
        try:
            from app.market_data.brsapi_provider import BrsapiProvider
            from app.config.settings import settings
            provider = BrsapiProvider(settings.brsapi_url, settings.brsapi_key)
            overview = provider.get_market_overview()
            gold, dollar = overview.get("gold", 0), overview.get("dollar", 0)
            if not gold and not dollar:
                return "❌ قیمت طلا/دلار موقتاً در دسترس نیست (سرویس BrsApi پاسخ معتبر نداد)."
            return f"🏅 طلا: {gold:,.0f} تومان\n💵 دلار: {dollar:,.0f} تومان"
        except Exception as e:
            return f"❌ خطا: {e}"

    def _handle_buy_query(self, text: str) -> str:
        """
        ===== اصلاح ریشه‌ای: Flow واقعی و قابل‌ردیابی برای «الان چی بخرم؟» =====
        این متد دقیقاً همان زنجیره‌ای را که باید اجرا شود پیاده‌سازی می‌کند و
        هر Node را برای Debug در لاگ ثبت می‌کند (پیشوند [BUY-FLOW]):
            Intent → Portfolio Tool → Market Data Tool → News Tool →
            Opportunity Analysis → AI Decision → RiskManager → Response

        اصول رعایت‌شده:
        - هیچ قیمت/عددی توسط AI ساخته نمی‌شود؛ همه از get_market_comparison
          (که خودش از market_data_manager.get_all_prices/get_historical واقعی
          می‌آید) و از پرتفولیوی واقعی کاربر گرفته می‌شود.
        - AI مجبور است چند دارایی (candidates) را واقعاً مقایسه کند - نه فقط
          یک نماد را حدس بزند - و اگر نمادی خارج از candidates برگرداند، آن
          تصمیم رد می‌شود (به AI_DECISION رجوع کنید).
        - RiskManager همیشه به‌عنوان آخرین Guardrail بعد از BUY اجرا می‌شود؛
          اگر رد کند یا در دسترس نباشد، تصمیم نهایی به WAIT برمی‌گردد و دلیل
          واقعی (نه هاردکد) نشان داده می‌شود.
        - اگر هر Node داده‌ی واقعی برنگرداند، این صریحاً به کاربر گفته می‌شود؛
          هیچ‌جا وانمود نمی‌شود که تحلیلی انجام شده که در واقع انجام نشده.
        - هیچ سفارش/معامله‌ی واقعی در این متد ثبت نمی‌شود (فقط توصیه‌ی متنی).
        """
        trace: List[Dict[str, Any]] = []

        def log_node(name: str, status: str, detail: Any = None):
            trace.append({"node": name, "status": status, "detail": detail})
            log.info(f"[BUY-FLOW] node={name} status={status} detail={str(detail)[:300]}")

        log_node("INTENT", "ok", {"text": text})

        if not self.advisor:
            log_node("AI_DECISION", "unavailable", "advisor=None")
            return "🤖 AI در دسترس نیست، پس نمی‌توانم تحلیل واقعی برای خرید انجام بدهم."

        # ----- Node: Portfolio Tool -----
        portfolio_summary = None
        if self.portfolio_mgr:
            try:
                snapshot = self.portfolio_mgr.fetch_snapshot()
                portfolio_summary = {
                    "total_value_usdt": snapshot.total_value_usdt,
                    "available_usdt": snapshot.available_usdt,
                    "percentages": snapshot.percentages,
                    "held_assets": list(snapshot.balances.keys()),
                }
                log_node("PORTFOLIO_TOOL", "ok", portfolio_summary)
            except Exception as e:
                log_node("PORTFOLIO_TOOL", "error", str(e))
        else:
            log_node("PORTFOLIO_TOOL", "unavailable", "portfolio_mgr=None")

        if not portfolio_summary:
            return ("❌ نتوانستم وضعیت واقعی کیف‌پول را بخوانم، پس نمی‌توانم مسئولانه پیشنهاد خرید بدهم "
                    "(این خطا واقعی است، نه یک پاسخ ثابت). لطفاً بعداً دوباره امتحان کنید.")

        # ----- Node: Market Data Tool + Opportunity Analysis -----
        try:
            comparison = self.advisor.get_market_comparison()
            if "error" in comparison:
                log_node("MARKET_DATA_TOOL", "error", comparison["error"])
            else:
                log_node("MARKET_DATA_TOOL", "ok", {"num_candidates": len(comparison.get("candidates", []))})
        except Exception as e:
            comparison = {"error": str(e), "candidates": []}
            log_node("MARKET_DATA_TOOL", "error", str(e))

        candidates = comparison.get("candidates", [])
        if not candidates:
            log_node("OPPORTUNITY_ANALYSIS", "unavailable", comparison.get("error"))
            return (f"❌ Market Data Tool داده‌ی واقعی برنگرداند ({comparison.get('error', 'نامشخص')})، "
                    "پس نمی‌توانم چند دارایی را واقعاً مقایسه کنم. تحلیل انجام نشد؛ این یک صبر ثابت نیست.")

        opportunities = comparison.get("opportunities", [])
        log_node("OPPORTUNITY_ANALYSIS", "ok", {"num_opportunities": len(opportunities)})

        # ----- Node: News Tool -----
        try:
            news = self.advisor.get_news(limit=3)
            if "error" in news:
                log_node("NEWS_TOOL", "error", news["error"])
            else:
                log_node("NEWS_TOOL", "ok", {"num_headlines": len(news.get("headlines", []))})
        except Exception as e:
            news = {"error": str(e)}
            log_node("NEWS_TOOL", "error", str(e))

        # ----- Node: AI Decision (deterministic - فقط تحلیل، بدون ساختن عدد) -----
        ai_context = {"portfolio": portfolio_summary, "candidates": comparison, "news": news}
        try:
            ai_result = self.advisor.decide_buy_recommendation(ai_context)
        except Exception as e:
            log.warning(f"decide_buy_recommendation raised: {e}")
            ai_result = None

        if ai_result is None:
            log_node("AI_DECISION", "unavailable_or_invalid")
            return ("🤖 نتوانستم یک تصمیم معتبر از تحلیل AI بگیرم (پاسخ نامعتبر بود یا نمادی خارج از "
                    "دارایی‌های واقعاً بررسی‌شده انتخاب شده بود)، پس صادقانه می‌گویم که الان نمی‌توانم "
                    "توصیه‌ی قابل‌اتکا بدهم. این یک «صبر» از‌پیش‌نوشته‌شده نیست؛ تحلیل واقعی انجام شد "
                    "ولی به تصمیم معتبر نرسید.")

        log_node("AI_DECISION", "ok", ai_result)
        action, reason, symbol = ai_result["action"], ai_result["reason"], ai_result.get("symbol")

        # ----- Node: RiskManager (آخرین Guardrail) -----
        risk_note = None
        if action == "BUY":
            candidate = next((c for c in candidates if c["symbol"] == symbol), None)
            if not candidate or not self.risk_mgr:
                log_node("RISK_MANAGER", "unavailable", {"symbol": symbol, "risk_mgr_present": bool(self.risk_mgr)})
                action = "WAIT"
                reason = (f"AI پیشنهاد خرید {symbol} را داد ({reason}) ولی چون RiskManager در دسترس نبود "
                          "یا قیمت معتبر آن پیدا نشد، به‌عنوان آخرین Guardrail این خرید تأیید نشد.")
            else:
                try:
                    market_symbol = self._symbol_resolver.resolve(symbol, "USDT")
                except Exception:
                    market_symbol = f"{symbol}_USDT"

                signal = Signal(
                    market=market_symbol, action=Action.BUY, reason=reason,
                    current_price=candidate["price"], entry_price=candidate["price"],
                )
                current_asset_exposure_usdt = (candidate.get("held_amount", 0) or 0) * candidate["price"]
                total_value_usdt = portfolio_summary["total_value_usdt"]
                available_usdt = portfolio_summary["available_usdt"]
                total_exposure_usdt = max(total_value_usdt - available_usdt, 0.0)

                try:
                    from app.config.settings import settings as app_settings
                    decision = self.risk_mgr.approve(
                        signal=signal,
                        portfolio_value_usdt=total_value_usdt,
                        available_usdt=available_usdt,
                        current_asset_exposure_usdt=current_asset_exposure_usdt,
                        total_exposure_usdt=total_exposure_usdt,
                        # ===== توجه: این پروژه (نه فقط این متد) هنوز ابزار واقعی
                        # عمق سفارش (orderbook depth) ندارد؛ همان قراردادی که در
                        # حلقه‌ی اصلی main.py هم استفاده شده (جایگزینی با
                        # settings.min_liquidity) اینجا هم عیناً تکرار می‌شود -
                        # چیز جدیدی جعل نشده، فقط با رفتار موجود پروژه یکسان شده. =====
                        orderbook_liquidity_usdt=app_settings.min_liquidity,
                        estimated_slippage_percent=signal.slippage_percent,
                        price_age_seconds=0.0,
                    )
                    log_node("RISK_MANAGER", "ok", {"approved": decision.approved, "reason": decision.reason})
                except Exception as e:
                    log_node("RISK_MANAGER", "error", str(e))
                    decision = None

                if decision is None:
                    action = "WAIT"
                    reason = f"AI پیشنهاد خرید {symbol} را داد ({reason}) ولی بررسی RiskManager با خطا مواجه شد."
                elif not decision.approved:
                    action = "WAIT"
                    reason = f"AI پیشنهاد خرید {symbol} را داد ({reason}) ولی RiskManager رد کرد: {decision.reason}"
                else:
                    risk_note = f"حداکثر حجم پیشنهادی RiskManager: {decision.max_position_usdt:,.2f} USDT ({decision.reason})"
        else:
            log_node("RISK_MANAGER", "skipped", "AI action was WAIT, no trade to risk-check")

        # ----- Node: Response -----
        lines = [f"🟢 پیشنهاد: خرید {symbol}" if action == "BUY" else "🟡 صبر", f"دلیل: {reason}"]
        lines.append(f"\nدارایی‌های واقعی بررسی‌شده: {', '.join(c['symbol'] for c in candidates)}")
        if opportunities:
            opp_lines = [f"- {o['symbol']}: {o['action']} ({o['change_percent_24h']:+.1f}%) - {o['reason']}" for o in opportunities]
            lines.append("فرصت‌های واقعی یافت‌شده:\n" + "\n".join(opp_lines))
        else:
            lines.append("در این بررسی، هیچ‌کدام از دارایی‌ها از آستانه‌ی فرصت واقعی عبور نکردند.")

        if news.get("headlines"):
            lines.append("\n📰 اخبار بررسی‌شده: " + "؛ ".join(h["title"] for h in news["headlines"][:3]))
        elif "error" in news:
            lines.append(f"\n📰 اخبار: در دسترس نبود ({news['error']})")

        if risk_note:
            lines.append(f"\n🛡️ {risk_note}")

        lines.append("\n⚠️ این فقط یک توصیه‌ی تحلیلی است؛ هیچ معامله‌ی واقعی ثبت نشده است.")

        log_node("RESPONSE", "ok")
        log.info(f"[BUY-FLOW] full_trace={json.dumps(trace, ensure_ascii=False, default=str)}")
        return "\n".join(lines)

    def _get_ai_response(self, text: str) -> str:
        if not self.advisor:
            return "🤖 AI در دسترس نیست."

        prices = {}
        for symbol in self.watchlist[:self.max_assets]:
            try:
                ticker = self.client.get_ticker(symbol)
                if isinstance(ticker, list) and ticker:
                    prices[symbol] = float(ticker[0].get("price", 0))
            except:
                pass

        portfolio = {}
        if self.portfolio_mgr:
            try:
                snapshot = self.portfolio_mgr.fetch_snapshot()
                portfolio = {asset: bal.total for asset, bal in snapshot.balances.items()}
            except:
                pass

        try:
            return self.advisor.get_recommendation({"prices": prices}, portfolio)
        except Exception as e:
            return f"❌ خطا: {e}"
