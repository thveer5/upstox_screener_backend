"""Sanity-check the screener endpoint against the upstream API.

Usage:
    cd backend
    python -m scripts.test_screener
"""
import asyncio
import json
import sys
from pathlib import Path

# allow running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.screener import fetch_movers  # noqa: E402


async def main() -> None:
    for kind in ("gainers", "losers"):
        print(f"\n=== {kind.upper()} (top 5) ===")
        try:
            data = await fetch_movers(kind=kind, page_size=5)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        rows = data.get("data") or data.get("result") or data
        print(json.dumps(rows, indent=2)[:1500])


if __name__ == "__main__":
    asyncio.run(main())
