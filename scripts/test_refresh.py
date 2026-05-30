"""Probe the Upstox internal refresh-access-token endpoint.

Goal: confirm what Set-Cookie headers come back so we know which tokens
to capture and how to reuse them.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

REFRESH_URL = "https://service.upstox.com/login/open/v3/auth/refresh-access-token"
REFRESH_PARAMS = {
    "client_id": "UTV-31qeuxlapso4wnmg07869yzi",
    "response_type": "token",
    "redirect_uri": "https://tv.upstox.com",
}

BROWSER_HEADERS = {
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


def _parse_cookie_header(s: str) -> dict:
    out = {}
    for pair in s.split(";"):
        if "=" in pair:
            k, v = pair.strip().split("=", 1)
            out[k] = v
    return out


async def main() -> None:
    cookie = get_settings().upstox_tv_cookie
    if not cookie:
        print("ERROR: UPSTOX_TV_COOKIE not set in .env")
        return

    cookies = _parse_cookie_header(cookie)
    print(f"Loaded {len(cookies)} cookies from .env")
    print(f"  has access_token   : {'access_token' in cookies}")
    print(f"  has refresh_token  : {'refresh_token' in cookies}")
    print(f"  has auth_identity_token: {'auth_identity_token' in cookies}")
    print()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        resp = await client.post(
            REFRESH_URL,
            params=REFRESH_PARAMS,
            cookies=cookies,
            headers=BROWSER_HEADERS,
        )

    print(f"=== REFRESH RESPONSE ===")
    print(f"HTTP {resp.status_code}")
    print(f"\n--- Response headers ---")
    for k, v in resp.headers.items():
        if k.lower() == "set-cookie":
            # show name + first 30 chars of value
            head = v.split(";", 1)[0]
            name = head.split("=", 1)[0]
            val_preview = head.split("=", 1)[1][:30] + "..." if "=" in head else ""
            attrs = v.split(";", 1)[1].strip() if ";" in v else ""
            print(f"  set-cookie: {name}={val_preview}   ATTRS: {attrs}")
        else:
            print(f"  {k}: {v[:120]}")

    print(f"\n--- Response body ---")
    print(resp.text[:500])

    print(f"\n--- All cookies received (parsed) ---")
    for c in resp.cookies.jar:
        val_preview = c.value[:30] + "..." if c.value else ""
        print(f"  {c.name}={val_preview}   domain={c.domain} path={c.path} expires={c.expires}")


if __name__ == "__main__":
    asyncio.run(main())
