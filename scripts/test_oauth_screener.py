"""Does the OAuth v2 Bearer token work on the internal screener endpoint?

If yes -> we can drop the cookie hack entirely and use OAuth for everything.
If no  -> we need to capture & call Upstox's internal refresh endpoint.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.token_store import get_access_token  # noqa: E402

PAYLOAD = {
    "query": "ttv > 1000000 and exchange = 'NSE'",
    "segment": "EQ",
    "fields": ["instrument_key", "symbol", "ltp", "change_percent"],
    "sort": [{"field": "change_percent", "direction": "desc"}],
    "pageSize": 3,
    "group": None,
    "subQuery": None,
}


async def main() -> None:
    token = get_access_token()
    if not token:
        print("ERROR: no OAuth token. Visit http://localhost:8000/auth/login first.")
        return

    print(f"OAuth token (first 30 chars): {token[:30]}...\n")

    # 4 different ways the server might accept the OAuth token
    attempts = [
        ("Bearer only",          {"Authorization": f"Bearer {token}"}),
        ("Bearer + Origin",      {"Authorization": f"Bearer {token}", "Origin": "https://tv.upstox.com", "Referer": "https://tv.upstox.com/"}),
        ("Cookie access_token",  {"Cookie": f"access_token={token}", "Origin": "https://tv.upstox.com", "Referer": "https://tv.upstox.com/"}),
        ("Both Bearer + Cookie", {"Authorization": f"Bearer {token}", "Cookie": f"access_token={token}", "Origin": "https://tv.upstox.com", "Referer": "https://tv.upstox.com/"}),
    ]

    base_headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        for name, extra in attempts:
            headers = {**base_headers, **extra}
            try:
                resp = await client.post(
                    "https://service.upstox.com/jscreener-api/v1/screener",
                    json=PAYLOAD,
                    headers=headers,
                )
                print(f"[{name}] HTTP {resp.status_code}")
                body = resp.text[:300]
                print(f"  body: {body}\n")
            except Exception as e:
                print(f"[{name}] ERROR: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
