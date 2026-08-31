# Bitpin Trading Bot — Phase 1 (OBSERVE + PAPER)

## ⚠️ وضعیت فعلی این تحویل (خیلی مهم)

این **فاز ۱** از یه پروژه‌ی ۲۵ بخشی خیلی بزرگه، نه محصول نهایی. چیزی که ساخته شده:

- ساختار کامل ماژولار پروژه (client بیت‌پین، پرتفولیو، استراتژی، ریسک‌منیجر، پیپر تریدینگ، دیتابیس، نوتیفیکیشن، لاگینگ)
- حالت **OBSERVE** کاملاً کاربردی: می‌خونه پرتفولیوی واقعی شما، بازارهاشو پیدا می‌کنه، سیگنال تولید می‌کنه، به تلگرام می‌فرسته — **صفر معامله**
- حالت **PAPER**: مثل OBSERVE + شبیه‌سازی معامله با RiskManager — **صفر سفارش واقعی**
- **LIVE عمداً کار نمی‌کنه.** `main.py` فقط لاگ می‌کنه که سیگنال LIVE اومده و متوقف می‌شه؛ `app/execution/live.py` نوشته شده ولی به main وصل نشده — این یه تصمیم عمدیه تا بدون بازبینی شما فعال نشه.

### چیزی که هنوز verify نشده (قبل از LIVE حتماً لازمه)
مسیر دقیق endpointهای زیر رو فقط از SDKهای غیررسمی (که به مستندات رسمی ارجاع می‌دن) استنباط کردم، نه مستقیم از هر صفحه‌ی docs.bitpin.ir:
- مسیر دقیق لاگین/توکن (`/api/v1/usr/api/login/`) و انقضای توکن
- مسیر دقیق wallets، markets، ticker، orderbook، order creation و پارامترهای دقیقشون
- کارمزد (fee) واقعی حساب شما — الان فرض شده ۰.۳۵٪
- هر WebSocket رسمی (اصلاً پیاده نشده؛ به‌جاش REST polling با retry/backoff استفاده شده، طبق دستور خودتون در بخش ۶)

چیزی که **مستقیم از docs.bitpin.ir تأیید شده**: base URL (`https://api.bitpin.ir` یا `https://api.bitpin.org`)، `Authorization: Bearer <token>`، و endpoint ارزها (`/api/v1/market/currencies`).

## چیکار باید بکنید

### ۱. ساخت API Key در بیت‌پین
به بخش تنظیمات API حساب بیت‌پین برید و یه کلید با **کمترین دسترسی لازم** بسازید — فقط خواندن بازار/کیف‌پول و ثبت سفارش، **بدون دسترسی برداشت (withdrawal)**. مسیر دقیق پنل رو خودتون توی حساب بیت‌پین ببینید (این رباتی که ساختم بهش دسترسی نداره).

### ۲. جای گذاشتن اطلاعات
```
cp .env.example .env
```
و مقادیر `BITPIN_API_KEY` و `BITPIN_API_SECRET` رو داخل `.env` بذارید — **هرگز اینا رو توی چت به من ندید.**

### ۳. نصب و اجرا
```bash
pip install -r requirements.txt
python main.py
```
پیش‌فرض `BOT_MODE=OBSERVE` است — امن‌ترین حالت، صفر معامله.

### ۴. تلگرام (اختیاری)
یه بات با @BotFather بسازید، توکنشو و chat_id خودتون رو توی `.env` بذارید.

### ۵. حالت PAPER
```
BOT_MODE=PAPER
```
رو توی `.env` تنظیم کنید و دوباره اجرا کنید.

### ۶. LIVE (فعلاً غیرفعال، عمداً)
حتی اگه `LIVE_TRADING=true` و `LIVE_TRADING_CONFIRMED=true` رو ست کنید، `main.py` فعلی سفارش واقعی نمی‌فرسته چون به‌عمد به `execution/live.py` وصل نشده. این کار **فاز بعدیه** — بعد از اینکه همه‌ی endpointهای بالا رو مستقیم روی docs.bitpin.ir تأیید کردیم و PAPER mode رو حداقل چند روز روی حساب واقعی تست کردیم.

## ساختار پروژه
```
trading_bot/
    app/bitpin/        client.py, auth.py, models.py
    app/portfolio/      manager.py (پرتفولیو), discovery.py (کشف بازار پویا)
    app/strategies/     base.py, initial_strategy.py
    app/risk/           manager.py (RiskManager — همه‌چیز از این رد میشه)
    app/execution/      paper.py (فعال), live.py (نوشته شده، وصل نشده)
    app/notifications/  telegram.py, sms.py
    app/database/       repository.py (SQLite)
    app/monitoring/      logger.py (secrets رو حذف می‌کنه از لاگ)
    app/ai/              analyzer.py (فقط خواندن، بدون کنترل معامله)
    app/config/          settings.py
    tests/
    main.py
    .env.example
```

## چیزی که هنوز باقی مونده (فاز‌های بعدی)
- Verify دقیق و مستقیم هر endpoint روی docs.bitpin.ir (فاز ۲)
- Backtesting framework (بخش ۱۹)
- تکمیل execution/live.py و وصل کردنش بعد از verify
- محاسبه‌ی liquidity واقعی از عمق orderbook (الان placeholder است)
- تست‌های بیشتر برای edge caseهای بخش ۲۲ (rate limit، قطعی شبکه، partial fill و ...)
