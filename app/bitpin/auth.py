import re
import time
import logging
import threading
import requests

log = logging.getLogger(__name__)


class BitpinAuthError(Exception):
    pass


# ===== اصلاح: مدیریت صحیح 429 (Too Many Requests) روی Login =====
# باگ قبلی: هر بار که get_token صدا زده می‌شد و توکن معتبری در حافظه
# نبود (مثلاً همان اولین بار، یا بعد از یک Login ناموفق)، بلافاصله و
# بدون هیچ فاصله‌ای دوباره POST /usr/authenticate/ می‌زد. وقتی بیت‌پین با
# 429 جواب می‌داد، توکنی ذخیره نمی‌شد، پس درخواست بعدیِ کیف پول (از یک
# thread دیگر مثل چت یا حلقه‌ی intelligence، یا حتی همون thread چند ثانیه
# بعد) دوباره بدون فاصله Login می‌زد و دوباره 429 می‌گرفت - همین چرخه باعث
# می‌شد کل گزارش پرتفولیو با خطای خام "Login failed: 429 ..." از کار
# بیفتد. راه‌حل: کش کردن توکن + یک قفل برای جلوگیری از Loginهای همزمان +
# یک بازه‌ی backoff که تا پایانش اصلاً درخواست شبکه‌ی جدیدی برای Login زده
# نمی‌شود (نه حتی یک درخواست دیگر).
DEFAULT_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300  # سقف exponential backoff (۵ دقیقه)


class BitpinAuth:
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self._access_token = None
        self._refresh_token = None
        self._access_expires_at = 0

        # ===== اصلاح: جلوگیری از چند Login همزمان از threadهای مختلف =====
        # (چت تلگرام، حلقه‌ی intelligence، و حلقه‌ی اصلی همگی همین client/auth
        # مشترک را صدا می‌زنند)
        self._lock = threading.Lock()

        # ===== اصلاح: رعایت 429 / Retry-After با exponential backoff =====
        self._blocked_until = 0.0  # قبل از این timestamp، اصلاً تلاش برای Login نکن
        self._backoff_seconds = DEFAULT_BACKOFF_SECONDS

    def _token_is_valid(self) -> bool:
        return bool(self._access_token) and time.time() < self._access_expires_at - 30

    def get_token(self, session: requests.Session) -> str:
        # مسیر سریع: اگه توکن معتبر داریم، همون رو برگردون - بدون قفل هم
        # این فقط یک خواندن ساده است و نیازی به Login جدید ندارد.
        if self._token_is_valid():
            return self._access_token

        with self._lock:
            # ===== double-checked locking: ممکنه تا وقتی منتظر قفل بودیم،
            # یه thread دیگه همین الان توکن رو تازه کرده باشه؛ در این حالت
            # نباید دوباره Login بزنیم. =====
            if self._token_is_valid():
                return self._access_token
            return self._login(session)

    def _login(self, session: requests.Session) -> str:
        if not self.api_key or not self.api_secret:
            raise BitpinAuthError("API credentials missing")

        # ===== اصلاح اصلی: اگه هنوز توی بازه‌ی backoff یک 429 قبلی هستیم،
        # اصلاً درخواست شبکه‌ی جدیدی نزن (نه اینکه فوراً دوباره تلاش کنیم و
        # احتمالاً دوباره 429 بگیریم). یک خطای واضح و سریع برمی‌گردانیم تا
        # caller (مثلاً PortfolioManager) بتواند به‌شکل graceful آن را
        # مدیریت کند، بدون اینکه به بیت‌پین درخواست اضافه‌ای زده باشیم.
        now = time.time()
        if now < self._blocked_until:
            wait_left = self._blocked_until - now
            raise BitpinAuthError(
                f"Login temporarily rate-limited by Bitpin (429); retry in {wait_left:.0f}s"
            )

        url = f"{self.base_url}/api/v1/usr/authenticate/"
        try:
            resp = session.post(
                url, json={"api_key": self.api_key, "secret_key": self.api_secret}, timeout=10
            )
        except requests.RequestException as e:
            # خطای شبکه‌ای (نه 429) - همون رفتار قبلی: raise کن تا retry
            # عمومی در client.py مدیریتش کنه.
            raise BitpinAuthError(f"Login request failed: {e}")

        if resp.status_code == 429:
            wait = self._parse_retry_after(resp)
            self._blocked_until = time.time() + wait
            # ===== exponential backoff با سقف مشخص برای دفعات بعدی =====
            self._backoff_seconds = min(self._backoff_seconds * 2, MAX_BACKOFF_SECONDS)
            log.warning(
                f"⚠️ Bitpin login throttled (429). No new login attempt for ~{wait:.0f}s."
            )
            raise BitpinAuthError(
                f"Login failed: 429 Request was throttled. Retry in {wait:.0f}s"
            )

        if resp.status_code != 200:
            raise BitpinAuthError(f"Login failed: {resp.status_code} {resp.text}")

        data = resp.json()
        access = data.get("access")
        if not access:
            raise BitpinAuthError(f"Login response missing access token: {resp.text[:200]}")

        self._access_token = access
        self._refresh_token = data.get("refresh", self._refresh_token)
        # ===== موفقیت: backoff و بازه‌ی مسدودسازی را ریست کن =====
        self._backoff_seconds = DEFAULT_BACKOFF_SECONDS
        self._blocked_until = 0.0
        # اگه API طول عمر توکن رو در پاسخ اعلام کرده باشه از همون استفاده
        # کن؛ در غیر این صورت همون مقدار پیش‌فرض قبلی (۳۰۰ ثانیه) حفظ می‌شود.
        expires_in = data.get("access_expires_in") or data.get("expires_in") or 300
        try:
            expires_in = float(expires_in)
        except (TypeError, ValueError):
            expires_in = 300
        self._access_expires_at = time.time() + expires_in
        return self._access_token

    def _parse_retry_after(self, resp: requests.Response) -> float:
        """
        مدت انتظار قبل از تلاش بعدی Login را تعیین می‌کند - اول از هدر
        استاندارد Retry-After، بعد از متن پیام بیت‌پین (مثلاً "Expected
        available in 36 seconds")، و در نهایت از backoff نمایی داخلی
        (با سقف MAX_BACKOFF_SECONDS) استفاده می‌شود.
        """
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 1.0)
            except ValueError:
                pass

        try:
            match = re.search(r"available in\s+(\d+(?:\.\d+)?)\s*second", resp.text, re.IGNORECASE)
            if match:
                return max(float(match.group(1)), 1.0)
        except Exception:
            pass

        return min(self._backoff_seconds, MAX_BACKOFF_SECONDS)

    def auth_header(self, session: requests.Session) -> dict:
        return {"Authorization": f"Bearer {self.get_token(session)}"}
