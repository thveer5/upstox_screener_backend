"""Proxy wrapper around the internal Upstox screener endpoint.

POST https://service.upstox.com/jscreener-api/v1/screener

CORS is locked to https://tv.upstox.com — we call it server-side. Auth is via
the `access_token` cookie. We get one from tv_session, which auto-rotates it
using the stored refresh_token. On 401 we force a refresh and retry once.
"""
from typing import Literal, Optional

import httpx
from fastapi import HTTPException

from .candle_cache import enrich_movers
from .tv_session import get_tv_session

SCREENER_URL = "https://service.upstox.com/jscreener-api/v1/screener"

DEFAULT_FIELDS = [
    "instrument_key",
    "symbol",
    "exchange",
    "ltp",
    "open",
    "high",
    "low",
    "change",
    "change_percent",
    "vtt",
    "ttv",
    "market_cap",
    "last_updated_at",
]


def _browser_headers(access_token: str) -> dict:
    return {
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
        "Cookie": f"access_token={access_token}",
    }


Movers = Literal["gainers", "losers"]


def _build_query(extra_filter: Optional[str] = None) -> str:
    if extra_filter:
        return f"ttv > 1000000 and {extra_filter} and exchange = 'NSE'"
    return "ttv > 1000000 and exchange = 'NSE'"


async def _post_screener(payload: dict) -> dict:
    """POST a payload to the screener with one 401-triggered token refresh + retry.
    Returns the unwrapped `data` object (with `instruments`)."""
    session = get_tv_session()

    resp = None
    for attempt in range(2):
        if attempt == 1:
            await session.refresh()  # force fresh token before retry
        token = await session.get_access_token()
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                SCREENER_URL, json=payload, headers=_browser_headers(token)
            )
        if resp.status_code != 401:
            break

    assert resp is not None
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Screener upstream error: {resp.text[:500]}",
        )

    body = resp.json()
    if not body.get("success", True):
        raise HTTPException(status_code=502, detail=body.get("error") or body)

    return body.get("data", body)


async def fetch_movers(
    kind: Movers = "gainers",
    page_size: int = 50,
    index_filter: Optional[str] = None,
) -> dict:
    payload = {
        "query": _build_query(index_filter),
        "segment": "EQ",
        "fields": DEFAULT_FIELDS,
        "sort": [{"field": "change_percent", "direction": "desc" if kind == "gainers" else "asc"}],
        "pageSize": page_size,
        "group": None,
        "subQuery": None,
    }
    data = await _post_screener(payload)
    instruments = data.get("instruments") or []
    if instruments:
        await enrich_movers(instruments)
    return data


def _sanitize_search(text: str) -> str:
    """Keep only characters that can legitimately appear in an NSE symbol/name.
    Strips quotes so the term can't break out of the SQL-ish `like` clause."""
    allowed = [ch for ch in text if ch.isalnum() or ch in " &-."]
    return "".join(allowed).strip()[:32]


async def search_instruments(query_text: str, page_size: int = 25) -> dict:
    """Symbol search across all NSE equities (no liquidity filter), so stocks
    that aren't in the current gainers/losers list are still findable.
    Same response shape and enrichment as fetch_movers."""
    safe = _sanitize_search(query_text)
    if not safe:
        return {"instruments": []}
    payload = {
        "query": f"(symbol like '%{safe}%' or name like '%{safe}%') and exchange = 'NSE'",
        "segment": "EQ",
        "fields": DEFAULT_FIELDS,
        "sort": [{"field": "ttv", "direction": "desc"}],
        "pageSize": page_size,
        "group": None,
        "subQuery": None,
    }
    data = await _post_screener(payload)
    instruments = data.get("instruments") or []
    if instruments:
        await enrich_movers(instruments)
    return data
