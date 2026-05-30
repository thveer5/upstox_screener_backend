"""Order placement against Upstox APIs.

Public API (api.upstox.com, OAuth Bearer):
  - place_order        : regular Market/Limit (entry orders)
  - place_gtt          : single SL/Target or OCO via documented v3 GTT
  - list_gtt / cancel_gtt / modify_gtt_sl_trigger

Internal API (service.upstox.com, tv_session cookie):
  - place_trailing_gtt : GTT WITH native trailing SL (Upstox handles the trailing
                         server-side via `trailingTicks`). The public v3 GTT API
                         has no trailing parameter, so we use the same endpoint
                         that pro.upstox.com's web UI uses.
"""
import time as _time
import uuid
from typing import Literal, Optional

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from .token_store import get_access_token
from .tv_session import get_tv_session

V2_PLACE_ORDER_URL = "https://api.upstox.com/v2/order/place"
V3_GTT_PLACE_URL = "https://api.upstox.com/v3/order/gtt/place"
V3_GTT_CANCEL_URL = "https://api.upstox.com/v3/order/gtt/cancel"
V3_GTT_MODIFY_URL = "https://api.upstox.com/v3/order/gtt/modify"
V3_GTT_LIST_URL = "https://api.upstox.com/v3/order/gtt"

# Internal pro.upstox.com endpoint that powers trailing SL in the web UI
INTERNAL_ORDER_URL = "https://service.upstox.com/order-api/v0/order"

ProductType = Literal["I", "D"]  # I = Intraday (MIS), D = Delivery (CNC)
TransactionType = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT", "SL", "SL-M"]


def _headers() -> dict:
    token = get_access_token()
    if not token:
        raise HTTPException(
            401,
            "Not logged in via Upstox OAuth. Visit /auth/login first — orders need the v2 Bearer token.",
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


class PlaceOrderRequest(BaseModel):
    instrument_token: str = Field(..., description="e.g. NSE_EQ|INE848E01016")
    transaction_type: TransactionType
    quantity: int = Field(..., ge=1)
    product: ProductType = "D"
    order_type: OrderType = "MARKET"
    price: float = 0.0  # required for LIMIT
    trigger_price: float = 0.0  # required for SL / SL-M
    validity: Literal["DAY", "IOC"] = "DAY"
    disclosed_quantity: int = 0
    is_amo: bool = False
    tag: Optional[str] = None


class GTTRequest(BaseModel):
    instrument_token: str
    transaction_type: TransactionType  # usually SELL for SL/Target on a long
    quantity: int = Field(..., ge=1)
    product: ProductType = "D"
    stoploss_trigger: Optional[float] = Field(default=None, gt=0)
    target_trigger: Optional[float] = Field(default=None, gt=0)


async def place_order(req: PlaceOrderRequest) -> dict:
    """Place a regular order via Upstox v2 API."""
    body = {
        "quantity": req.quantity,
        "product": req.product,
        "validity": req.validity,
        "price": req.price,
        "tag": req.tag or "",
        "instrument_token": req.instrument_token,
        "order_type": req.order_type,
        "transaction_type": req.transaction_type,
        "disclosed_quantity": req.disclosed_quantity,
        "trigger_price": req.trigger_price,
        "is_amo": req.is_amo,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(V2_PLACE_ORDER_URL, json=body, headers=_headers())

    payload = _parse_response(resp)
    return {"upstox_request": body, **payload}


async def place_gtt(req: GTTRequest) -> dict:
    """Place a GTT (Good Till Triggered) order for SL / Target protection.

    Upstox model: each rule is an ENTRY strategy with direction BELOW (stop loss)
    or ABOVE (target). When the market crosses the trigger, the configured
    transaction (typically SELL for a long position) is placed.

    - Only stoploss_trigger  -> SINGLE GTT, ENTRY BELOW
    - Only target_trigger    -> SINGLE GTT, ENTRY ABOVE
    - Both                   -> MULTIPLE GTT (OCO: whichever fires first cancels the other)
    """
    rules = []
    if req.stoploss_trigger is not None:
        rules.append({
            "strategy": "ENTRY",
            "trigger_type": "BELOW",
            "trigger_price": req.stoploss_trigger,
        })
    if req.target_trigger is not None:
        rules.append({
            "strategy": "ENTRY",
            "trigger_type": "ABOVE",
            "trigger_price": req.target_trigger,
        })
    if not rules:
        raise HTTPException(400, "GTT needs at least one of stoploss_trigger or target_trigger")

    body = {
        "type": "MULTIPLE" if len(rules) > 1 else "SINGLE",
        "quantity": req.quantity,
        "product": req.product,
        "rules": rules,
        "instrument_token": req.instrument_token,
        "transaction_type": req.transaction_type,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(V3_GTT_PLACE_URL, json=body, headers=_headers())

    payload = _parse_response(resp)
    return {"upstox_request": body, **payload}


async def list_gtt() -> dict:
    """List all currently-active GTT orders for the authenticated user."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(V3_GTT_LIST_URL, headers=_headers())
    return _parse_response(resp)


async def modify_gtt_sl_trigger(gtt_order_id: str, new_sl_trigger: float, quantity: int) -> dict:
    """Modify a GTT to update only the BELOW (SL) trigger price.

    Used by the trailing-SL job to ratchet the stop upward as the price rises.
    We send a SINGLE rule because we're only adjusting the SL leg.
    """
    body = {
        "gtt_order_id": gtt_order_id,
        "type": "SINGLE",
        "quantity": quantity,
        "rules": [{
            "strategy": "ENTRY",
            "trigger_type": "BELOW",
            "trigger_price": new_sl_trigger,
        }],
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.put(V3_GTT_MODIFY_URL, json=body, headers=_headers())
    return _parse_response(resp)


async def cancel_gtt(gtt_order_id: str) -> dict:
    """Cancel a GTT by id. Upstox v3 expects DELETE with the id in the JSON body."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.request(
            "DELETE",
            V3_GTT_CANCEL_URL,
            json={"gtt_order_id": gtt_order_id},
            headers=_headers(),
        )
    return _parse_response(resp)


class TrailingGTTRequest(BaseModel):
    """Trailing-SL GTT on an existing holding.

    trail_type='amount' means trail by absolute rupees (e.g. 0.50 = ₹0.50).
    trail_type='percent' means trail by % of LTP (e.g. 1.5 = 1.5%).

    avg_price comes from the holding's average buy price — used as the
    `rules.value` and `price` fields in Upstox's payload (matching the
    pattern from pro.upstox.com's web UI).
    """
    instrument_token: str
    quantity: int = Field(..., ge=1)
    product: Literal["I", "D"] = "D"
    avg_price: float = Field(..., gt=0)
    current_ltp: float = Field(..., gt=0)
    sl_trigger: float = Field(..., gt=0, description="initial SL trigger price")
    trail_type: Literal["amount", "percent"]
    trail_value: float = Field(..., gt=0)
    sl_mpp: int = Field(default=2, description="Market Protection Premium %")


def _trail_to_ticks(trail_type: str, trail_value: float, ltp: float) -> int:
    """Convert user-friendly trail value (% or ₹) to Upstox's `trailingTicks`.

    Empirically (from a captured pro.upstox.com request): ₹0.13 trailing gap
    serialised as `trailingTicks: 13` -> 1 tick = ₹0.01 regardless of
    the instrument's actual tick size. So we just multiply by 100.
    """
    if trail_type == "amount":
        rupees = trail_value
    else:  # percent
        rupees = ltp * trail_value / 100
    ticks = int(round(rupees * 100))
    return max(1, ticks)


async def place_trailing_gtt(req: TrailingGTTRequest) -> dict:
    """Place a GTT with native trailing SL via Upstox's internal pro.upstox.com API.

    Uses tv_session cookie auth (NOT the public OAuth Bearer token) — the
    internal endpoint authenticates the same way as the screener.
    """
    session = get_tv_session()
    access_token = await session.get_access_token()

    trailing_ticks = _trail_to_ticks(req.trail_type, req.trail_value, req.current_ltp)

    body = {
        "data": {
            "context": "PW3",
            "requestMode": "REGULAR",
            "orders": [{
                "instrumentKey": req.instrument_token,
                "orderCategory": "CONDITIONAL_HOLDING",
                "amo": False,
                "quantity": req.quantity,
                "orderType": "L",
                "productType": req.product,
                "side": "BUY",  # matches the working captured payload — annotates the underlying long position
                "validity": "GTT",
                "rules": [{
                    "compareWith": "LTP",
                    "condition": "BELOW",
                    "value": str(req.avg_price),
                }],
                "additionalInfo": {
                    "surveillance": True,
                    "itemId": str(uuid.uuid4()),
                    "orderLtp": req.current_ltp,
                },
                "price": req.avg_price,
                "stopLossTrigger": req.sl_trigger,
                "stopLossMPP": req.sl_mpp,
                "trailingTicks": trailing_ticks,
            }],
            "orderTime": int(_time.time() * 1000),
        }
    }

    headers = {
        "Origin": "https://pro.upstox.com",
        "Referer": "https://pro.upstox.com/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cookie": f"access_token={access_token}",
    }

    # Try once; on 401, force-refresh tv_session and retry
    resp = None
    for attempt in range(2):
        if attempt == 1:
            await session.refresh()
            headers["Cookie"] = f"access_token={await session.get_access_token()}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(INTERNAL_ORDER_URL, json=body, headers=headers)
        if resp.status_code != 401:
            break

    assert resp is not None
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Trailing GTT placement failed: {resp.text[:500]}",
        )
    return {
        "upstox_request": body,
        "upstox_response": resp.json(),
        "trailing_ticks_used": trailing_ticks,
    }


def _parse_response(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code,
            detail={"upstox_error": body, "status": resp.status_code},
        )
    return {"upstox_response": body, "status": resp.status_code}
