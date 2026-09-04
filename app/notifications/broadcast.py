import logging
from .telegram import TelegramNotifier

log = logging.getLogger(__name__)

class BroadcastNotifier:
    def __init__(self, telegram: TelegramNotifier):
        self.telegram = telegram

    def send_signal(self, message: str):
        log.info(f"SIGNAL: {message}")
        if self.telegram:
            self.telegram.send_message(f"📊 {message}")

    def send_opportunity(self, message: str):
        log.info(f"OPPORTUNITY: {message}")
        if self.telegram:
            self.telegram.send_message(f"💎 {message}")

    def send_intelligence_report(self, message: str):
        log.info(f"INTELLIGENCE: {message}")
        if self.telegram:
            self.telegram.send_message(f"🧠 {message}")

    def send_error(self, message: str):
        log.error(f"ERROR: {message}")
        if self.telegram:
            self.telegram.send_message(f"⚠️ {message}")

    def send_do_not_trade(self, message: str):
        log.info(f"DO NOT TRADE: {message}")
        if self.telegram:
            self.telegram.send_message(f"🚫 {message}")

    def send_paper_trade(self, message: str):
        log.info(f"PAPER TRADE: {message}")
        if self.telegram:
            self.telegram.send_message(f"📄 {message}")
