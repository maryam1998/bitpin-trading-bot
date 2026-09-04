import logging
import requests

log = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        self.last_update_id = None

    def send_message(self, text: str):
        if not self.enabled:
            log.info("Telegram not enabled")
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            log.error(f"Telegram send error: {e}")
            return False

    def get_updates(self, offset=None, timeout=25):
        if not self.enabled:
            return []
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            params = {"timeout": timeout}
            if offset:
                params["offset"] = offset
            resp = requests.get(url, params=params, timeout=timeout+5)
            if resp.status_code == 200:
                return resp.json().get("result", [])
        except Exception as e:
            log.error(f"Get updates error: {e}")
        return []

    def send_to(self, chat_id: str, text: str):
        if not self.enabled:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            log.error(f"Send to error: {e}")
            return False
