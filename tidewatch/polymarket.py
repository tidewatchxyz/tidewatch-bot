"""Prediction market data from Polymarket's public read APIs (no key needed).

Endpoints:
  https://gamma-api.polymarket.com/markets         market metadata and current odds
  https://clob.polymarket.com/prices-history        price history for one outcome token
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .http import Fetcher

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


@dataclass(frozen=True)
class Market:
    question: str
    slug: str
    outcomes: list[str]
    prices: list[float]
    token_ids: list[str]
    volume_24h: float
    end_date: str | None

    @property
    def is_binary_yes_no(self) -> bool:
        return [o.lower() for o in self.outcomes] == ["yes", "no"] and len(self.token_ids) == 2

    @property
    def yes_price(self) -> float | None:
        return self.prices[0] if self.is_binary_yes_no and self.prices else None


@dataclass(frozen=True)
class PricePoint:
    at: datetime
    price: float


def _json_list(value: Any) -> list:
    """Gamma returns some list fields as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def parse_market(m: dict) -> Market | None:
    try:
        prices = [float(p) for p in _json_list(m.get("outcomePrices"))]
    except (TypeError, ValueError):
        prices = []
    outcomes = [str(o) for o in _json_list(m.get("outcomes"))]
    tokens = [str(t) for t in _json_list(m.get("clobTokenIds"))]
    if not m.get("question") or not outcomes:
        return None
    vol = m.get("volume24hr") or m.get("volume24hrClob") or 0
    try:
        vol = float(vol)
    except (TypeError, ValueError):
        vol = 0.0
    return Market(
        question=m["question"].strip(),
        slug=m.get("slug", ""),
        outcomes=outcomes,
        prices=prices,
        token_ids=tokens,
        volume_24h=vol,
        end_date=m.get("endDate"),
    )


def parse_history(raw: dict) -> list[PricePoint]:
    pts = []
    for row in raw.get("history", []) if isinstance(raw, dict) else []:
        try:
            pts.append(PricePoint(at=datetime.fromtimestamp(int(row["t"]), tz=timezone.utc),
                                  price=float(row["p"])))
        except (KeyError, TypeError, ValueError):
            continue
    pts.sort(key=lambda p: p.at)
    return pts


def fetch_top_markets(fetcher: Fetcher, limit: int = 20) -> list[Market]:
    raw = fetcher.get_json(
        f"{GAMMA}/markets",
        {"active": "true", "closed": "false", "order": "volume24hr",
         "ascending": "false", "limit": limit},
        name="polymarket_top_markets",
    )
    markets = [parse_market(m) for m in raw if isinstance(m, dict)]
    found = [m for m in markets if m is not None]
    # Sort locally too, in case the API ignores the order parameter.
    found.sort(key=lambda m: m.volume_24h, reverse=True)
    return found


def fetch_market(fetcher: Fetcher, slug: str) -> Market:
    raw = fetcher.get_json(f"{GAMMA}/markets", {"slug": slug}, name=f"polymarket_market_{slug}")
    for m in raw if isinstance(raw, list) else []:
        parsed = parse_market(m)
        if parsed is not None:
            return parsed
    raise LookupError(f"No Polymarket market with slug '{slug}'")


def fetch_history(fetcher: Fetcher, token_id: str, days: int = 30) -> list[PricePoint]:
    raw = fetcher.get_json(
        f"{CLOB}/prices-history",
        {"market": token_id, "interval": "1m", "fidelity": 180},
        name="polymarket_history",
    )
    pts = parse_history(raw)
    if len(pts) < 2:  # fall back to an explicit time window
        now = int(time.time())
        raw = fetcher.get_json(
            f"{CLOB}/prices-history",
            {"market": token_id, "startTs": now - days * 86400, "endTs": now, "fidelity": 180},
            name="polymarket_history_window",
        )
        pts = parse_history(raw)
    return pts
