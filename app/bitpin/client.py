"""
Bitpin REST API client.

Base URL and Bearer-token auth scheme are VERIFIED against the official
docs at https://docs.bitpin.ir (fetched directly in this session).

Endpoint paths below are marked either VERIFIED (confirmed directly from
docs.bitpin.ir in this session) or UNVERIFIED (inferred from third-party
SDKs that reference the official docs, but not independently re-checked
page-by-page here). Do not enable LIVE trading until every UNVERIFIED
endpoint used by your configuration has been checked against the current
docs.bitpin.ir pages.
"""
import time
import logging
import requests

from app.bitpin.auth import BitpinAuth, BitpinAuthError

log = logging.getLogger("bitpin.client")


class BitpinAPIError(Exception):
    pass


class BitpinClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.auth = BitpinAuth(base_url, api_key, api_secret)

    # ---------- low-level request with retry/backoff ----------
    def _request(self, method: str, path: str, auth_required: bool = False,
                 params: dict = None, json_body: dict = None, max_retries: int = 3):
        url = self.base_url + path
        headers = {}
        if auth_required:
            headers.update(self.auth.auth_header(self.session))

        last_exc = None
        for attempt in range(max_retries):
            try:
                resp = self.session.request(
                    method, url, params=params, json=json_body,
                    headers=headers, timeout=self.timeout,
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    log.warning("Bitpin rate limited, backing off %ss", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = 2 ** attempt
                    log.warning("Bitpin server error %s, retrying in %ss", resp.status_code, wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 400:
                    raise BitpinAPIError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
                return resp.json()
            except requests.RequestException as e:
                last_exc = e
                wait = 2 ** attempt
                log.warning("Network error calling Bitpin (%s), retrying in %ss", e, wait)
                time.sleep(wait)
        raise BitpinAPIError(f"Failed {method} {path} after {max_retries} retries: {last_exc}")

    # ---------- Public / market data (VERIFIED base pattern) ----------
    def get_currencies(self):
        """VERIFIED: GET /api/v1/market/currencies (shown directly in official docs)."""
        return self._request("GET", "/api/v1/mkt/currencies/")

    def get_markets(self):
        """UNVERIFIED exact path: pattern /api/v1/mkt/markets/ per third-party
        SDKs built against the official docs. Confirm against docs.bitpin.ir
        before relying on this in LIVE mode."""
        return self._request("GET", "/api/v1/mkt/markets/")

    def get_ticker(self, symbol: str = None):
        """UNVERIFIED exact path. Returns price list for one or all markets."""
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v1/mkt/tickers/", params=params)

    def get_orderbook(self, symbol: str):
        """UNVERIFIED exact path."""
        return self._request("GET", f"/api/v1/mkt/orderbook/{symbol}/")

    def get_recent_trades(self, symbol: str):
        """UNVERIFIED exact path."""
        return self._request("GET", f"/api/v1/mkt/trades/{symbol}/")

    # ---------- Account (requires auth) ----------
    def get_wallets(self):
        """UNVERIFIED exact path. Requires Bearer auth."""
        return self._request("GET", "/api/v1/wlt/wallets/", auth_required=True)

    def get_open_orders(self, symbol: str = None):
        """UNVERIFIED exact path. Requires Bearer auth."""
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/api/v1/odr/orders/", auth_required=True, params=params)

    def get_order_status(self, order_id: str):
        """UNVERIFIED exact path. Requires Bearer auth."""
        return self._request("GET", f"/api/v1/odr/orders/{order_id}/", auth_required=True)

    def get_fills(self, order_id: str = None):
        """UNVERIFIED exact path. Requires Bearer auth."""
        params = {"order_id": order_id} if order_id else None
        return self._request("GET", "/api/v1/odr/matches/", auth_required=True, params=params)

    # ---------- Trading (LIVE only — gated by ExecutionEngine, never called directly) ----------
    def create_order(self, symbol: str, side: str, order_type: str, amount: str,
                      price: str = None, client_order_id: str = None):
        """UNVERIFIED exact path/schema. This method must only ever be called
        by app/execution/live.py, and only after the RiskManager has approved
        the trade and LIVE mode has been explicitly enabled and confirmed.
        DO NOT call this directly from strategy or AI code."""
        body = {"symbol": symbol, "side": side, "type": order_type, "amount1": amount}
        if price:
            body["price"] = price
        if client_order_id:
            body["identifier"] = client_order_id
        return self._request("POST", "/api/v1/odr/orders/", auth_required=True, json_body=body)

    def cancel_order(self, order_id: str):
        """UNVERIFIED exact path. Only called by app/execution/live.py."""
        return self._request("DELETE", f"/api/v1/odr/orders/{order_id}/", auth_required=True)
