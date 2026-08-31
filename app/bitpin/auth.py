"""
Bitpin authentication.

VERIFIED from official docs (https://docs.bitpin.ir):
- Base URL: https://api.bitpin.ir or https://api.bitpin.org
- Authenticated requests use: Authorization: Bearer <token>

NOT YET independently verified in this session (must be confirmed against
docs.bitpin.ir's Authentication section before relying on them in LIVE mode):
- The exact login endpoint path that exchanges API key/secret for a token
- Access/refresh token expiry and refresh endpoint path

Third-party SDKs (unofficial) that reference the official docs show a pattern
of POSTing api_key/secret_key to obtain access+refresh JWTs, and refreshing
via a separate endpoint. This module is written against that pattern but
flags itself as UNVERIFIED so it fails loudly instead of guessing silently.
"""
import time
import requests

UNVERIFIED_LOGIN_PATH = "/api/v1/usr/api/login/"
UNVERIFIED_REFRESH_PATH = "/api/v1/usr/refresh_token/"


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
            raise BitpinAuthError(
                "No Bitpin API credentials configured (BITPIN_API_KEY / "
                "BITPIN_API_SECRET). Read-only public endpoints (markets, "
                "tickers, orderbook) do not require auth and can still be used."
            )
        url = self.base_url + UNVERIFIED_LOGIN_PATH
        resp = session.post(
            url,
            json={"api_key": self.api_key, "secret_key": self.api_secret},
            timeout=10,
        )
        if resp.status_code != 200:
            raise BitpinAuthError(
                f"Bitpin login failed ({resp.status_code}): {resp.text[:300]}. "
                f"NOTE: login endpoint path is unverified against current "
                f"docs.bitpin.ir — confirm it there before troubleshooting further."
            )
        data = resp.json()
        self._access_token = data.get("access")
        self._refresh_token = data.get("refresh")
        # Bitpin JWT lifetime is not yet verified here; assume a conservative
        # 5 minutes and re-login rather than trusting an unverified expiry.
        self._access_expires_at = time.time() + 300
        if not self._access_token:
            raise BitpinAuthError(f"Unexpected Bitpin login response shape: {data!r}")
        return self._access_token

    def auth_header(self, session: requests.Session) -> dict:
        return {"Authorization": f"Bearer {self.get_token(session)}"}
