

import base64
import time
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache

TOKEN_CACHE_KEY = "daraja_oauth_token"
TOKEN_TTL_SECONDS = 3400
REQUEST_ATTEMPTS = 3
REQUEST_BACKOFF_SECONDS = 1.5


class DarajaError(RuntimeError):
    """Raised when the Daraja gateway rejects a request."""


class DarajaClient:
    def __init__(self, env=None, consumer_key=None, consumer_secret=None,
                 shortcode=None, passkey=None, callback_url=None):
        self.env = env or getattr(settings, "DARAJA_ENV", "sandbox")
        self.base_url = (
            "https://sandbox.safaricom.co.ke"
            if self.env == "sandbox"
            else "https://api.safaricom.co.ke"
        )
        self.consumer_key = consumer_key or getattr(settings, "DARAJA_CONSUMER_KEY", "")
        self.consumer_secret = consumer_secret or getattr(settings, "DARAJA_CONSUMER_SECRET", "")
        self.shortcode = shortcode or getattr(settings, "DARAJA_SHORTCODE", "")
        self.passkey = passkey or getattr(settings, "DARAJA_PASSKEY", "")
        self.callback_url = callback_url or getattr(settings, "DARAJA_CALLBACK_URL", "")

    @staticmethod
    def _request(method: str, url: str, **kwargs) -> requests.Response:
        """HTTP call with retries for transient DNS/connectivity failures."""
        last_exc = None
        for attempt in range(1, REQUEST_ATTEMPTS + 1):
            try:
                return requests.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < REQUEST_ATTEMPTS:
                    time.sleep(REQUEST_BACKOFF_SECONDS * attempt)
        raise last_exc

    # OAuth
    def get_access_token(self) -> str:
        cached_token = cache.get(TOKEN_CACHE_KEY)
        if cached_token:
            return cached_token
        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        try:
            response = self._request(
                "GET", url, auth=(self.consumer_key, self.consumer_secret), timeout=15
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise DarajaError(f"Daraja OAuth failed: {exc}") from exc
        token = response.json().get("access_token")
        if not token:
            raise DarajaError("Daraja OAuth response contained no access_token")
        cache.set(TOKEN_CACHE_KEY, token, timeout=TOKEN_TTL_SECONDS)
        return token

    def _generate_password(self):
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        raw = f"{self.shortcode}{self.passkey}{timestamp}".encode("utf-8")
        return base64.b64encode(raw).decode("utf-8"), timestamp

    @staticmethod
    def _headers(token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # -- STK Push -------------------------------------------------------
    def initiate_stk_push(self, phone: str, amount, account_ref: str, desc: str) -> dict:
        token = self.get_access_token()
        password, timestamp = self._generate_password()
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": int(amount),
            "PartyA": phone,
            "PartyB": self.shortcode,
            "PhoneNumber": phone,
            "CallBackURL": self.callback_url,
            "AccountReference": account_ref[:12],   # <= 12 chars on the statement
            "TransactionDesc": desc[:13],           # <= 13 chars on the PIN prompt
        }
        try:
            res = self._request(
                "POST",
                f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                json=payload,
                headers=self._headers(token),
                timeout=20,
            )
        except requests.RequestException as exc:
            raise DarajaError(f"STK Push transport error: {exc}") from exc

        body = res.json() if res.content else {}
        if res.status_code >= 400 or "errorMessage" in body or "errorDescription" in body:
            raise DarajaError(
                body.get("errorMessage")
                or body.get("errorDescription")
                or f"STK Push failed with HTTP {res.status_code}"
            )
        return body

    # -- STK Query (fallback poller) ------------------------------------
    def query_stk_status(self, checkout_request_id: str) -> dict:
        token = self.get_access_token()
        password, timestamp = self._generate_password()
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        try:
            res = self._request(
                "POST",
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                json=payload,
                headers=self._headers(token),
                timeout=20,
            )
        except requests.RequestException as exc:
            raise DarajaError(f"STK Query transport error: {exc}") from exc
        body = res.json() if res.content else {}
        if res.status_code >= 400 or "errorMessage" in body:
            raise DarajaError(
                body.get("errorMessage") or f"STK Query failed with HTTP {res.status_code}"
            )
        return body
