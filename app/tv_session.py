"""Manages the tv.upstox.com session for the internal screener endpoint.

Lifecycle:
  - One-time bootstrap: user pastes their tv.upstox.com cookie once. We extract
    `refresh_token` from it (and the current `access_token` if present) and
    persist to tv_session.json.
  - Subsequent calls: get_access_token() returns the cached access_token if
    still valid, otherwise calls Upstox's refresh endpoint with refresh_token
    and rotates the access_token.

Token lifetimes (confirmed empirically):
  - access_token  : 1 hour (we refresh ~60 sec before expiry)
  - refresh_token : 24 hours from initial issuance (NOT rotated on refresh)
    -> User must re-bootstrap with a fresh cookie ~daily.

Cloud deployment: no browser involvement at runtime. Just HTTP.
"""
import json
import time
from base64 import urlsafe_b64decode
from pathlib import Path
from typing import Optional

import httpx
from fastapi import HTTPException

SESSION_FILE = Path(__file__).resolve().parent.parent / "tv_session.json"

REFRESH_URL = "https://service.upstox.com/login/open/v3/auth/refresh-access-token"
REFRESH_PARAMS = {
    "client_id": "UTV-31qeuxlapso4wnmg07869yzi",
    "response_type": "token",
    "redirect_uri": "https://tv.upstox.com",
}

_BROWSER_HEADERS = {
    "Origin": "https://tv.upstox.com",
    "Referer": "https://tv.upstox.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}


def _decode_jwt_exp(jwt: str) -> Optional[int]:
    """Pull `exp` (Unix seconds) out of a JWT without verifying the signature."""
    try:
        payload_b64 = jwt.split(".")[1]
        padding = (-len(payload_b64)) % 4
        payload = json.loads(urlsafe_b64decode(payload_b64 + "=" * padding))
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


def _parse_cookie_header(s: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in s.split(";"):
        if "=" in pair:
            k, v = pair.strip().split("=", 1)
            out[k] = v
    return out


class TVSession:
    def __init__(self) -> None:
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        if SESSION_FILE.exists():
            try:
                self._data = json.loads(SESSION_FILE.read_text())
            except json.JSONDecodeError:
                self._data = {}

    def save(self) -> None:
        SESSION_FILE.write_text(json.dumps(self._data, indent=2))

    @property
    def refresh_token(self) -> Optional[str]:
        return self._data.get("refresh_token")

    @property
    def access_token(self) -> Optional[str]:
        return self._data.get("access_token")

    @property
    def access_token_expires_at(self) -> int:
        return self._data.get("access_token_expires_at", 0)

    @property
    def refresh_token_expires_at(self) -> int:
        return self._data.get("refresh_token_expires_at", 0)

    def is_access_token_valid(self, safety_window_sec: int = 60) -> bool:
        if not self.access_token or not self.access_token_expires_at:
            return False
        return time.time() < self.access_token_expires_at - safety_window_sec

    def bootstrap_from_cookie(self, cookie_str: str) -> dict:
        """Initialize the session from a full Cookie header string captured from
        the browser. Only refresh_token is required; access_token is optional."""
        cookies = _parse_cookie_header(cookie_str)
        refresh = cookies.get("refresh_token")
        if not refresh:
            raise HTTPException(400, "Cookie string does not contain refresh_token")

        self._data = {
            "refresh_token": refresh,
            "refresh_token_expires_at": _decode_jwt_exp(refresh) or int(time.time() + 86400),
            "bootstrapped_at": int(time.time()),
        }
        access = cookies.get("access_token")
        if access:
            self._data["access_token"] = access
            self._data["access_token_expires_at"] = _decode_jwt_exp(access) or int(time.time() + 3600)
        self.save()
        return self.status()

    def bootstrap_from_refresh_token(self, refresh_token: str) -> dict:
        self._data = {
            "refresh_token": refresh_token,
            "refresh_token_expires_at": _decode_jwt_exp(refresh_token) or int(time.time() + 86400),
            "bootstrapped_at": int(time.time()),
        }
        self.save()
        return self.status()

    async def refresh(self) -> str:
        """Call the refresh endpoint, parse new tokens from Set-Cookie, persist."""
        if not self.refresh_token:
            raise HTTPException(
                401,
                "tv_session has no refresh_token. POST /auth/tv/bootstrap first "
                "with a fresh tv.upstox.com cookie.",
            )

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            resp = await client.post(
                REFRESH_URL,
                params=REFRESH_PARAMS,
                cookies={"refresh_token": self.refresh_token},
                headers=_BROWSER_HEADERS,
            )

        if resp.status_code != 200:
            raise HTTPException(
                401,
                f"Refresh failed (HTTP {resp.status_code}): {resp.text[:300]}. "
                "Your refresh_token has likely expired (~24h). Re-bootstrap with a "
                "fresh cookie from tv.upstox.com (POST /auth/tv/bootstrap).",
            )

        new_access = resp.cookies.get("access_token")
        if not new_access:
            raise HTTPException(
                502,
                f"Refresh returned 200 but no access_token in Set-Cookie. Body: {resp.text[:300]}",
            )

        self._data["access_token"] = new_access
        self._data["access_token_expires_at"] = _decode_jwt_exp(new_access) or int(time.time() + 3600)

        new_refresh = resp.cookies.get("refresh_token")
        if new_refresh and new_refresh != self.refresh_token:
            # Upstox doesn't currently rotate it, but be ready if they start.
            self._data["refresh_token"] = new_refresh
            self._data["refresh_token_expires_at"] = _decode_jwt_exp(new_refresh) or int(time.time() + 86400)

        self._data["last_refreshed_at"] = int(time.time())
        self.save()
        return new_access

    async def get_access_token(self) -> str:
        """Return a valid access_token, refreshing if needed."""
        if self.is_access_token_valid():
            return self.access_token  # type: ignore[return-value]
        return await self.refresh()

    def status(self) -> dict:
        now = int(time.time())
        return {
            "has_refresh_token": bool(self.refresh_token),
            "has_access_token": bool(self.access_token),
            "access_token_valid": self.is_access_token_valid(),
            "access_token_expires_in_sec": (
                max(0, self.access_token_expires_at - now) if self.access_token_expires_at else None
            ),
            "refresh_token_expires_in_sec": (
                max(0, self.refresh_token_expires_at - now) if self.refresh_token_expires_at else None
            ),
            "last_refreshed_at": self._data.get("last_refreshed_at"),
            "bootstrapped_at": self._data.get("bootstrapped_at"),
        }


_instance: Optional[TVSession] = None


def get_tv_session() -> TVSession:
    """Singleton. Auto-bootstraps from UPSTOX_TV_COOKIE on first call if no session file."""
    global _instance
    if _instance is None:
        _instance = TVSession()
        if not _instance.refresh_token:
            # Try one-time auto-bootstrap from .env (for first-run / migration)
            from .config import get_settings
            env_cookie = get_settings().upstox_tv_cookie
            if env_cookie:
                try:
                    _instance.bootstrap_from_cookie(env_cookie)
                except HTTPException:
                    pass
    return _instance
