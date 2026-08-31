"""
SMS notification service — critical events ONLY (real trade executed,
emergency stop, daily loss limit, severe failure, abnormal market, security
failure). Provider is pluggable via SMS_PROVIDER; no vendor is hard-coded.

Only a generic HTTP-POST provider adapter is implemented here as a
starting point — configure SMS_PROVIDER's actual endpoint/payload format
once you pick a provider, since that is not something Bitpin's docs cover.
"""
import logging
import requests

log = logging.getLogger("notifications.sms")


class SMSNotifier:
    def __init__(self, provider: str, api_key: str, api_secret: str, phone_number: str):
        self.provider = provider
        self.api_key = api_key
        self.api_secret = api_secret
        self.phone_number = phone_number
        self.enabled = bool(provider and api_key and phone_number)
        if not self.enabled:
            log.info("SMS not configured — critical-event SMS disabled.")

    def send_critical(self, text: str, endpoint_url: str = None):
        if not self.enabled:
            log.debug("SMS disabled, would have sent: %s", text)
            return
        if not endpoint_url:
            log.warning("No SMS provider endpoint configured; skipping send. "
                        "Set your provider's API URL before relying on SMS alerts.")
            return
        try:
            requests.post(
                endpoint_url,
                json={"api_key": self.api_key, "secret": self.api_secret,
                      "to": self.phone_number, "text": text},
                timeout=10,
            )
        except requests.RequestException as e:
            log.error("SMS send failed: %s", e)
