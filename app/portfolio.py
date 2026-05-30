"""Holdings + Positions wrappers around Upstox v2 portfolio APIs.

  - holdings  : long-term (CNC) — stocks you've actually bought and own
  - positions : intraday (MIS) + carry-forward — open day positions

Both require the OAuth Bearer token (same as orders).
"""
import httpx
from fastapi import HTTPException

from .token_store import get_access_token

HOLDINGS_URL = "https://api.upstox.com/v2/portfolio/long-term-holdings"
POSITIONS_URL = "https://api.upstox.com/v2/portfolio/short-term-positions"


def _headers() -> dict:
    token = get_access_token()
    if not token:
        raise HTTPException(
            401,
            "Not logged in via Upstox OAuth. Visit /auth/login first.",
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


async def _fetch(url: str) -> dict:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=_headers())
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail={"upstox_error": body})
    return body


async def get_holdings() -> dict:
    body = await _fetch(HOLDINGS_URL)
    # Upstox response: { status: 'success', data: [...] }
    return {"holdings": body.get("data", []), "raw": body}


async def get_positions() -> dict:
    body = await _fetch(POSITIONS_URL)
    return {"positions": body.get("data", []), "raw": body}
