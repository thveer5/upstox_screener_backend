import httpx
from fastapi import HTTPException

from .config import get_settings
from .token_store import get_access_token


def _auth_headers() -> dict:
    token = get_access_token()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated. Visit /auth/login first.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


async def exchange_code_for_token(code: str) -> dict:
    settings = get_settings()
    data = {
        "code": code,
        "client_id": settings.upstox_api_key,
        "client_secret": settings.upstox_api_secret,
        "redirect_uri": settings.upstox_redirect_uri,
        "grant_type": "authorization_code",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(settings.upstox_token_url, data=data, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Token exchange failed: {resp.text}",
        )
    return resp.json()


async def get_profile() -> dict:
    settings = get_settings()
    url = f"{settings.upstox_api_base}/user/profile"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=_auth_headers())
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()
