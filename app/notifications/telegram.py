"""
Telegram notification service. Silently no-ops if not configured — never
raises just because notifications aren't set up.
"""
import logging
import requests

log = logging.getLogger("notifications.telegram")


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        if not self.enabled:
            log.info("Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing) — notifications disabled.")

    def send(self, text: str):
        if not self.enabled:
            log.debug("Telegram disabled, would have sent: %s", text)
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=10)
        except requests.RequestException as e:
            log.error("Telegram send failed: %s", e)

    # Convenience wrappers for the event types listed in the spec
    def send_opportunity(self, text): self.send(f"🟢 OPPORTUNITY\n{text}")
    def send_do_not_trade(self, text): self.send(f"🔴 DO NOT TRADE\n{text}")
    def send_paper_trade(self, text): self.send(f"📝 PAPER TRADE\n{text}")
    def send_live_trade(self, text): self.send(f"💰 LIVE TRADE\n{text}")
    def send_risk_warning(self, text): self.send(f"⚠️ RISK WARNING\n{text}")
    def send_critical(self, text): self.send(f"🚨 {text}")
    def send_error(self, text): self.send(f"❌ ERROR\n{text}")
