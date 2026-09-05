import logging
from typing import Dict, Any, List
from app.strategies.base import Action

log = logging.getLogger(__name__)

class ChatHandler:
    def __init__(self, client, discovery, engine, watchlist, max_assets=10, advisor=None, portfolio_mgr=None):
        self.client = client
        self.discovery = discovery
        self.engine = engine
        self.watchlist = watchlist
        self.max_assets = max_assets
        self.advisor = advisor
        self.portfolio_mgr = portfolio_mgr

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

        else:
            return self._get_ai_response(text)

    def _get_portfolio_info(self) -> str:
        """دریافت اطلاعات کیف پول با محاسبه‌ی available = balance - frozen"""
        try:
            wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
            if not wallets:
                return "❌ اطلاعات کیف پول در دسترس نیست."

            # دریافت قیمت USDT/IRT
            try:
                ticker = self.client.get_ticker("USDT_IRT")
                if isinstance(ticker, list):
                    ticker = next((t for t in ticker if t.get("symbol") == "USDT_IRT"), {})
                usdt_irt_price = float(ticker.get("price", 0))
            except:
                usdt_irt_price = 0.0

            if usdt_irt_price <= 0:
                return "❌ قیمت USDT/IRT در دسترس نیست."

            # ===== اصلاح: محاسبه available از balance - frozen =====
            balances = {}
            for item in wallets:
                asset = item.get("asset", "")
                balance = float(item.get("balance", 0))
                frozen = float(item.get("frozen", 0))
                # فیلد available واقعاً 0 برمی‌گرداند، پس محاسبه می‌کنیم
                available = float(item.get("available") or (balance - frozen))
                if balance > 0:
                    balances[asset] = {"balance": balance, "available": available}
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
                        ticker_asset = self.client.get_ticker(f"{asset}_USDT")
                        if isinstance(ticker_asset, list):
                            ticker_asset = next((t for t in ticker_asset if t.get("symbol") == f"{asset}_USDT"), {})
                        price_usdt = float(ticker_asset.get("price", 0))
                        if price_usdt > 0:
                            value_irt = balance * price_usdt * usdt_irt_price
                        else:
                            ticker_irt = self.client.get_ticker(f"{asset}_IRT")
                            if isinstance(ticker_irt, list):
                                ticker_irt = next((t for t in ticker_irt if t.get("symbol") == f"{asset}_IRT"), {})
                            price_irt = float(ticker_irt.get("price", 0))
                            if price_irt > 0:
                                value_irt = balance * price_irt
                    except Exception:
                        value_irt = None

                if value_irt is None:
                    unpriced_assets.append(asset)
                    continue

                asset_values_irt[asset] = value_irt
                total_irt += value_irt

            total_usdt = total_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0

            return self._format_advisor_report(balances, asset_values_irt, unpriced_assets, total_irt, total_usdt, usdt_irt_price)

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
        if buy_opps:
            opp = buy_opps[0]
            action = "🟢 بررسی خرید"
            best_move = (
                f"{cash_ratio:.0f}٪ سرمایه‌ات نقد است. الان یک فرصت احتمالی روی "
                f"{opp['symbol']} دیده می‌شه ({opp.get('reason', '')}). "
                f"چون بخش زیادی از سرمایه‌ات نقده، اگه خواستی وارد بشی، بهتره مرحله‌ای باشه، نه یکجا."
            )
        elif sell_opps:
            opp = sell_opps[0]
            action = "🔴 بررسی برداشت سود"
            best_move = f"{opp['symbol']} که داری، {opp.get('reason', '')}. شاید وقت خوبی باشه بخشی از سود رو ذخیره کنی."
        elif cash_ratio >= 50:
            action = "🟡 صبر"
            best_move = (
                f"{cash_ratio:.0f}٪ سرمایه‌ات نقده و الان فرصت خرید به‌اندازه‌ای قوی در بازار دیده نمی‌شه که "
                "ورود فوری رو توجیه کنه؛ بهتره فعلاً صبر کنیم. اگه فرصت مناسبی پیش بیاد، بهت خبر می‌دم."
            )
        elif top_volatile_pct >= 50:
            action = "🔴 کاهش تمرکز"
            best_move = f"بیشتر سرمایه‌ت روی {top_volatile[0]} متمرکزه؛ بهتره برای کاهش ریسک بخشی از پرتفولیو رو متنوع‌تر کنی."
        else:
            action = "🟡 صبر"
            best_move = "ترکیب فعلی پرتفولیو نسبتاً متعادله؛ فعلاً نیازی به تغییر فوری نیست. اگه فرصت مناسبی پیش بیاد، بهت خبر می‌دم."
        lines.append(f"{action}\n{best_move}")

        lines.append("\n⚠️ **ریسک پرتفولیو**")
        lines.append(f"{liquidity.split()[0]} نقدینگی: {liquidity.split()[1] if len(liquidity.split())>1 else ''}")
        lines.append(f"{diversity.split()[0]} تنوع: {' '.join(diversity.split()[1:])}")
        lines.append(f"{risk_level.split()[0]} ریسک فعلی: {risk_level.split()[1]}")

        lines.append("\n💡 این تحلیل ترکیب دو چیزه: دارایی‌های خودت و وضعیت لحظه‌ای بازار - نه فقط حدس. تضمینی در کار نیست، فقط راهنماییه.")


        return "\n".join(lines)

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
