import time
import requests

class BitpinAuthError(Exception):
    pass

class BitpinAuth:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self._access_token = None
        self._refresh_token = None
        self._access_expires_at = 0

    def get_token(self, session: requests.Session) -> str:
        if self._access_token and time.time() < self._access_expires_at - 30:
            return self._access_token
        return self._login(session)

    def _login(self, session: requests.Session) -> str:
        if not self.api_key or not self.api_secret:
            raise BitpinAuthError("API credentials missing")
        url = f"{self.base_url}/api/v1/usr/authenticate/"
        resp = session.post(url, json={"api_key": self.api_key, "secret_key": self.api_secret}, timeout=10)
        if resp.status_code != 200:
            raise BitpinAuthError(f"Login failed: {resp.status_code} {resp.text}")
        data = resp.json()
        self._access_token = data.get("access")
        self._access_expires_at = time.time() + 300
        return self._access_token

    def auth_header(self, session: requests.Session) -> dict:
        return {"Authorization": f"Bearer {self.get_token(session)}"}
