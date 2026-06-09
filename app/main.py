from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from pydantic import BaseModel

from .config import get_settings
from .indices import build_index_filter, list_indices
from .orders import (
    GTTRequest,
    PlaceOrderRequest,
    TrailingGTTRequest,
    cancel_gtt,
    list_gtt,
    place_gtt,
    place_order,
    place_trailing_gtt,
)
from .portfolio import get_holdings, get_positions
from .candle_cache import get_recent_daily_candles, init_candle_db
from .screener import fetch_movers
from .token_store import clear_token, load_token, save_token
from .tv_session import get_tv_session
from .upstox_client import exchange_code_for_token, get_profile

settings = get_settings()
init_candle_db()

app = FastAPI(title="Algo Upstox Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    token = load_token()
    status = "authenticated" if token else "not authenticated"
    login_link = '<a href="/auth/login">Login with Upstox</a>'
    return f"""
    <html>
      <head><title>Algo Upstox</title></head>
      <body style="font-family: sans-serif; max-width: 640px; margin: 40px auto;">
        <h1>Algo Upstox Backend</h1>
        <p>Status: <b>{status}</b></p>
        <p>{login_link}</p>
        <ul>
          <li><a href="/auth/status">/auth/status (OAuth)</a></li>
          <li><a href="/me">/me (OAuth profile)</a></li>
          <li><a href="/auth/tv/status">/auth/tv/status (tv session)</a></li>
          <li><a href="/api/screener/movers?kind=gainers">/api/screener/movers?kind=gainers</a></li>
          <li><a href="/api/screener/movers?kind=losers">/api/screener/movers?kind=losers</a></li>
          <li><a href="/docs">/docs (OpenAPI)</a></li>
        </ul>
      </body>
    </html>
    """


@app.get("/auth/login")
async def auth_login():
    """Kick off OAuth — redirects the user to the Upstox authorize page."""
    params = {
        "response_type": "code",
        "client_id": settings.upstox_api_key,
        "redirect_uri": settings.upstox_redirect_uri,
    }
    url = f"{settings.upstox_auth_url}?{urlencode(params)}"
    return RedirectResponse(url)


@app.get("/auth/callback")
async def auth_callback(
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
):
    if error:
        raise HTTPException(status_code=400, detail=f"{error}: {error_description}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    token_payload = await exchange_code_for_token(code)
    save_token(token_payload)
    frontend = settings.cors_origins[0] if settings.cors_origins else "/"
    return RedirectResponse(frontend)


def _jwt_exp(jwt: str) -> int | None:
    """Return JWT exp (Unix seconds) without verifying signature, or None."""
    try:
        import base64
        import json as _json
        payload_b64 = jwt.split(".")[1]
        payload_b64 += "=" * ((-len(payload_b64)) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return int(exp) if exp else None
    except Exception:
        return None


@app.get("/auth/status")
async def auth_status():
    import time as _time
    token = load_token()
    if not token:
        return {"authenticated": False, "valid": False, "reason": "no_token"}

    access = token.get("access_token", "")
    exp = _jwt_exp(access)
    now = int(_time.time())
    valid = bool(exp) and exp > now + 30  # 30 sec safety window

    return {
        "authenticated": valid,  # only true if not expired
        "valid": valid,
        "has_token": True,
        "expires_at": exp,
        "expires_in_sec": (exp - now) if exp else None,
        "user_id": token.get("user_id"),
        "user_name": token.get("user_name"),
        "broker": token.get("broker"),
        "reason": None if valid else "expired",
    }


@app.get("/auth/logout")
async def auth_logout():
    clear_token()
    frontend = settings.cors_origins[0] if settings.cors_origins else "/"
    return RedirectResponse(frontend)


@app.get("/me")
async def me():
    return await get_profile()


@app.get("/api/indices")
async def api_indices():
    """List of supported indices for the dashboard dropdown."""
    return {"indices": list_indices()}


@app.get("/api/screener/movers")
async def screener_movers(
    kind: str = Query(default="gainers", pattern="^(gainers|losers)$"),
    page_size: int = Query(default=50, ge=1, le=200),
    index: str | None = Query(default=None, description="Index id from /api/indices (e.g. nifty_midcap_100)"),
):
    """Top gainers / losers from the Upstox internal screener."""
    return await fetch_movers(
        kind=kind,
        page_size=page_size,
        index_filter=build_index_filter(index),
    )


@app.get("/api/screener/candles")
async def screener_candles(
    instrument_key: str = Query(..., description="e.g. NSE_EQ|INE..."),
    days: int = Query(default=10, ge=1, le=30),
):
    """Recent daily OHLC candles for one instrument (oldest -> newest).

    Used by the wishlist detail modal to show day-by-day price / change %.
    Returns up to `days + 1` candles so the client can compute a change %
    for the first day shown.
    """
    candles = await get_recent_daily_candles(instrument_key, limit=days + 1)
    return {"instrument_key": instrument_key, "candles": candles}


class BootstrapBody(BaseModel):
    cookie: str | None = None
    refresh_token: str | None = None


@app.post("/auth/tv/bootstrap")
async def auth_tv_bootstrap(body: BootstrapBody):
    """Initialize the tv.upstox.com session.

    Provide ONE of:
      - cookie: full Cookie header copy-pasted from DevTools (must contain refresh_token)
      - refresh_token: just the refresh_token JWT (no other cookies needed)

    Persists to tv_session.json so this only needs to happen once per ~24h
    (until the refresh_token expires).
    """
    session = get_tv_session()
    if body.cookie:
        return session.bootstrap_from_cookie(body.cookie)
    if body.refresh_token:
        return session.bootstrap_from_refresh_token(body.refresh_token)
    raise HTTPException(400, "Provide either `cookie` or `refresh_token`")


@app.post("/auth/tv/refresh")
async def auth_tv_refresh():
    """Force-refresh the access_token using the stored refresh_token."""
    session = get_tv_session()
    await session.refresh()
    return session.status()


@app.get("/auth/tv/status")
async def auth_tv_status():
    return get_tv_session().status()


@app.post("/api/orders/place")
async def api_place_order(body: PlaceOrderRequest):
    """Place a regular Market / Limit order via Upstox v2 API."""
    return await place_order(body)


@app.post("/api/orders/gtt")
async def api_place_gtt(body: GTTRequest):
    """Place a GTT (Good Till Triggered) order — single SL/Target or OCO with both."""
    return await place_gtt(body)


@app.get("/api/orders/gtt")
async def api_list_gtt():
    """List all active GTT orders."""
    return await list_gtt()


@app.delete("/api/orders/gtt/{gtt_order_id}")
async def api_cancel_gtt(gtt_order_id: str):
    """Cancel a GTT order by its id."""
    return await cancel_gtt(gtt_order_id)


@app.get("/api/portfolio/holdings")
async def api_holdings():
    """Long-term (CNC) holdings."""
    return await get_holdings()


@app.get("/api/portfolio/positions")
async def api_positions():
    """Short-term (intraday + carry-forward) positions."""
    return await get_positions()


_ip_cache: dict = {"ip": None, "fetched_at": 0}


@app.get("/api/system/ip")
async def api_system_ip():
    """Return the backend's public outbound IP (the one Upstox sees).

    Cached for 5 minutes so we don't hammer ipify.
    """
    import time as _time
    import httpx as _httpx
    now = int(_time.time())
    if _ip_cache["ip"] and now - _ip_cache["fetched_at"] < 300:
        return {"ip": _ip_cache["ip"], "cached": True, "age_sec": now - _ip_cache["fetched_at"]}
    try:
        async with _httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("https://api.ipify.org")
        ip = r.text.strip()
        _ip_cache["ip"] = ip
        _ip_cache["fetched_at"] = now
        return {"ip": ip, "cached": False}
    except Exception as e:
        return {"ip": _ip_cache["ip"], "error": str(e), "stale": True}


@app.post("/api/orders/trailing-gtt")
async def api_place_trailing_gtt(body: TrailingGTTRequest):
    """Place a GTT with native trailing stop-loss using Upstox's internal pro.upstox.com API.

    Upstox handles the trailing server-side via `trailingTicks` — no polling needed
    on our end. Uses the same tv_session cookie auth as the screener.
    """
    return await place_trailing_gtt(body)
