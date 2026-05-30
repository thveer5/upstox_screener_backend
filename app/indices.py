"""Known NSE index keys for the screener `query` field.

Format: index = 'NSE_INDEX|<label>' — confirmed by capturing the payload while
NIFTY MIDCAP 100 was selected in tv.upstox.com.

The label is the exact dropdown text from Upstox's UI. Other indices follow
the same pattern but spelling/case matters. If an index returns 0 results,
re-capture the payload for that index and update the key here.
"""
from typing import Optional

# id  -> { label, key }   id is the slug used in the React dropdown
INDICES: dict[str, dict[str, str]] = {
    "all": {"label": "All NSE", "key": ""},
    "nifty_50": {"label": "Nifty 50", "key": "Nifty 50"},
    "nifty_100": {"label": "Nifty 100", "key": "Nifty 100"},
    "nifty_200": {"label": "Nifty 200", "key": "Nifty 200"},
    "nifty_500": {"label": "Nifty 500", "key": "Nifty 500"},
    "nifty_midcap_100": {"label": "NIFTY MIDCAP 100", "key": "NIFTY MIDCAP 100"},
    "nifty_smlcap_100": {"label": "NIFTY SMLCAP 100", "key": "NIFTY SMLCAP 100"},
    "nifty_smlcap_250": {"label": "NIFTY SMLCAP 250", "key": "NIFTY SMLCAP 250"},
    "nifty_microcap_250": {"label": "NIFTY MICROCAP250", "key": "NIFTY MICROCAP250"},
    "nifty_largemid_250": {"label": "NIFTY LARGEMID250", "key": "NIFTY LARGEMID250"},
    "nifty_auto": {"label": "Nifty Auto", "key": "Nifty Auto"},
    "nifty_bank": {"label": "Nifty Bank", "key": "Nifty Bank"},
    "nifty_pvt_bank": {"label": "Nifty Pvt Bank", "key": "Nifty Pvt Bank"},
    "nifty_pharma": {"label": "Nifty Pharma", "key": "Nifty Pharma"},
    "nifty_fmcg": {"label": "Nifty FMCG", "key": "Nifty FMCG"},
    "nifty_it": {"label": "Nifty IT", "key": "Nifty IT"},
    "nifty_energy": {"label": "Nifty Energy", "key": "Nifty Energy"},
    "nifty_healthcare": {"label": "NIFTY HEALTHCARE", "key": "NIFTY HEALTHCARE"},
    "nifty_oil_gas": {"label": "NIFTY OIL AND GAS", "key": "NIFTY OIL AND GAS"},
    "nifty_fin_service": {"label": "Nifty Fin Service", "key": "Nifty Fin Service"},
    "nifty_infra": {"label": "Nifty Infra", "key": "Nifty Infra"},
    "nifty_commodities": {"label": "Nifty Commodities", "key": "Nifty Commodities"},
}


def build_index_filter(index_id: Optional[str]) -> Optional[str]:
    """Return the SQL fragment to AND into the screener query, or None."""
    if not index_id or index_id == "all":
        return None
    entry = INDICES.get(index_id)
    if not entry or not entry["key"]:
        return None
    # Single-quoted string in Upstox's SQL-ish query language
    return f"index = 'NSE_INDEX|{entry['key']}'"


def list_indices() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in INDICES.items()]
