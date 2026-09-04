def _get_portfolio_info(self) -> str:
    """دریافت اطلاعات کیف پول از بیت‌پین با محاسبه‌ی دقیق ارزش کل"""
    try:
        wallets = self.client._request("GET", "/api/v1/wlt/wallets/", auth_required=True)
        if not wallets:
            return "❌ اطلاعات کیف پول در دسترس نیست."

        # ۱. دریافت قیمت USDT/IRT
        try:
            ticker = self.client.get_ticker("USDT_IRT")
            if isinstance(ticker, list):
                ticker = next((t for t in ticker if t.get("symbol") == "USDT_IRT"), {})
            usdt_irt_price = float(ticker.get("price", 0))
        except:
            usdt_irt_price = 0.0

        if usdt_irt_price <= 0:
            return "❌ قیمت USDT/IRT در دسترس نیست."

        # ۲. استخراج موجودی‌ها
        balances = {}
        for item in wallets:
            asset = item.get("asset", "")
            balance = float(item.get("balance", 0))
            available = float(item.get("available", 0))
            if balance > 0:
                balances[asset] = {"balance": balance, "available": available}

        # ۳. محاسبه ارزش هر دارایی به تومان (IRT)
        total_irt = 0.0
        asset_values = {}

        for asset, data in balances.items():
            balance = data["balance"]
            if asset == "IRT":
                value_irt = balance
            elif asset == "USDT":
                value_irt = balance * usdt_irt_price
            else:
                # قیمت ارز به USDT
                try:
                    ticker_asset = self.client.get_ticker(f"{asset}_USDT")
                    if isinstance(ticker_asset, list):
                        ticker_asset = next((t for t in ticker_asset if t.get("symbol") == f"{asset}_USDT"), {})
                    price_usdt = float(ticker_asset.get("price", 0))
                    if price_usdt > 0:
                        value_irt = balance * price_usdt * usdt_irt_price
                    else:
                        # اگر بازار USDT وجود نداشت، از IRT استفاده کن
                        ticker_irt = self.client.get_ticker(f"{asset}_IRT")
                        if isinstance(ticker_irt, list):
                            ticker_irt = next((t for t in ticker_irt if t.get("symbol") == f"{asset}_IRT"), {})
                        price_irt = float(ticker_irt.get("price", 0))
                        value_irt = balance * price_irt if price_irt > 0 else 0.0
                except:
                    value_irt = 0.0

            asset_values[asset] = value_irt
            total_irt += value_irt

        # ۴. محاسبه مجموع به USDT
        total_usdt = total_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0

        # ۵. ساخت گزارش
        lines = ["📊 **وضعیت کیف پول:**"]

        for asset, data in balances.items():
            balance = data["balance"]
            available = data["available"]
            value_irt = asset_values.get(asset, 0.0)
            value_usdt = value_irt / usdt_irt_price if usdt_irt_price > 0 else 0.0
            lines.append(
                f"• {asset}: {balance:.2f} (قابل استفاده: {available:.2f}) "
                f"≈ {value_usdt:.2f} USDT"
            )

        lines.append(f"\n💰 مجموع: {total_usdt:.2f} USDT")
        lines.append(f"💰 معادل تومان: {total_irt:,.0f} IRT")

        return "\n".join(lines)

    except Exception as e:
        log.error(f"Portfolio error: {e}")
        return f"❌ خطا در دریافت کیف پول: {e}"
