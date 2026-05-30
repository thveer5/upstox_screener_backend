# Algo Upstox — Backend

FastAPI service that:
1. Handles **Upstox OAuth** (for official v2 API endpoints like `/me`, holdings, orders, etc.)
2. Wraps the **internal screener endpoint** at `service.upstox.com/jscreener-api/v1/screener` with automatic access-token rotation using a stored `refresh_token`.

## Why two auth flows?

The official v2 API (api.upstox.com) uses OAuth — that's what `/auth/login`, `/auth/callback`, `/me` use. But the **Market Watch screener** is an internal endpoint that doesn't accept v2 OAuth tokens. It uses the tv.upstox.com web-session cookies, which we manage via the `tv_session` module.

## Prerequisites

- Python 3.10+
- An Upstox app with redirect URL: `http://localhost:8000/auth/callback`

## Setup

```powershell
cd d:\algo_upstox\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # PowerShell — use .bat in cmd
pip install -r requirements.txt
```

`.env` should contain:
```
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
UPSTOX_REDIRECT_URI=http://localhost:8000/auth/callback

# Optional — only needed for first-run bootstrap of the tv session.
# After first run the tokens live in tv_session.json and this can be removed.
UPSTOX_TV_COOKIE=<paste full Cookie header from tv.upstox.com here>
```

## Run

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then visit http://localhost:8000

## First-time setup of the TV session

The screener needs a `refresh_token` from tv.upstox.com. You have two options:

### Option 1 — via `.env` (recommended first time)
1. Open tv.upstox.com → log in → F12 → Network → any request → Headers → copy the **Cookie** value
2. Paste it into `UPSTOX_TV_COOKIE` in `.env`
3. Start the backend — it auto-bootstraps on first call

### Option 2 — via the UI (anytime, no restart needed)
1. Open http://localhost:5173 → click **Bootstrap** in the header
2. Paste either the full Cookie header OR just the `refresh_token` JWT
3. Click Save

After bootstrap, `tv_session.json` is created and persists across restarts.

## How auto-refresh works

```
You call /api/screener/movers
        |
        v
tv_session.get_access_token()
    cached & valid? -> use it
    expired?        -> POST refresh-access-token with stored refresh_token
                       -> get new access_token from Set-Cookie
                       -> save to tv_session.json
                       -> return it
        |
        v
fetch from service.upstox.com  (with Cookie: access_token=<token>)
    401?  -> force one refresh, retry
    200?  -> return data
```

Token lifetimes:
- `access_token` — 1 hour (auto-rotated by us with ~60 sec safety window)
- `refresh_token` — 24 hours from initial issuance (NOT rotated, must re-bootstrap daily)

## Endpoints

| Path | What |
|------|------|
| `GET  /` | Landing page |
| `GET  /docs` | OpenAPI swagger |
| **OAuth (v2 API)** | |
| `GET  /auth/login` | Redirects to Upstox OAuth |
| `GET  /auth/callback` | OAuth callback |
| `GET  /auth/status` | OAuth token status |
| `GET  /auth/logout` | Clear OAuth token |
| `GET  /me` | Upstox `/user/profile` (uses OAuth) |
| **TV session (internal screener)** | |
| `POST /auth/tv/bootstrap` | Initialize TV session (body: `{cookie}` or `{refresh_token}`) |
| `POST /auth/tv/refresh` | Force-refresh the access_token |
| `GET  /auth/tv/status` | TV session status + expiry times |
| **Screener** | |
| `GET  /api/indices` | List of indices for the dropdown |
| `GET  /api/screener/movers?kind=gainers&index=nifty_midcap_100&page_size=50` | Top movers |

## Cloud deployment notes

The whole thing works in cloud with **no browser required at runtime**:

1. Deploy backend (FastAPI on whatever — Railway, Fly, ECS, K8s, etc.)
2. Use a **persistent volume** for `tv_session.json` (or swap the file storage in `tv_session.py` for Redis/DB)
3. **Once per day**: capture a fresh cookie from tv.upstox.com in your browser and POST it to `/auth/tv/bootstrap` (curl from your laptop is fine)
4. Between bootstraps, backend auto-rotates `access_token` every hour without your involvement

For a true zero-touch deployment, you could automate step 3 with a headless browser (Playwright) that logs in with your stored mobile+PIN+TOTP and POSTs the cookie. That's a future enhancement, not required for the dashboard to work.

## Files

```
app/
  main.py             # FastAPI routes
  config.py           # .env loading
  indices.py          # NSE index dropdown options + SQL fragment builder
  screener.py         # Wraps the screener endpoint
  tv_session.py       # refresh_token store + auto-rotate access_token  <-- the cloud magic
  token_store.py      # OAuth token file for v2 API
  upstox_client.py    # OAuth token exchange + /me
scripts/
  test_screener.py    # Quick gainers/losers sanity check
  test_oauth_screener.py   # Confirms OAuth tokens don't work on screener
  test_refresh.py     # Shows what Set-Cookie comes back from refresh
```
