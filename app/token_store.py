import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TOKEN_FILE = Path(__file__).resolve().parent.parent / "tokens.json"


def save_token(payload: dict) -> None:
    payload = {**payload, "saved_at": datetime.now(timezone.utc).isoformat()}
    TOKEN_FILE.write_text(json.dumps(payload, indent=2))


def load_token() -> Optional[dict]:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text())
    except json.JSONDecodeError:
        return None


def clear_token() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def get_access_token() -> Optional[str]:
    data = load_token()
    if not data:
        return None
    return data.get("access_token")
