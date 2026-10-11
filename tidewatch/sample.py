"""Synthetic, clearly fake data in the same JSON shapes the real APIs return.

Used by --sample so the charts can be rendered and checked offline. Every
chart made from this data carries a SAMPLE DATA stamp; never post these.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone


def stablecoins_raw(seed: int = 7) -> dict:
    rng = random.Random(seed)
    sizes = [168e9, 74e9, 12e9, 8.5e9, 6.1e9, 4.4e9, 3.2e9, 2.6e9, 1.9e9, 1.4e9, 0.9e9, 0.6e9]
    assets = []
    for i, size in enumerate(sizes):
        week = size * rng.uniform(-0.06, 0.07)
        price = 1 + rng.gauss(0, 0.0008)
        if i == 7:
            price = 0.9921  # one deliberately off-peg coin
        assets.append({
            "name": f"Demo Dollar {chr(65 + i)}",
            "symbol": f"DEMO-{chr(65 + i)}",
            "pegType": "peggedUSD",
            "pegMechanism": "fiat-backed",
            "circulating": {"peggedUSD": size},
            "circulatingPrevDay": {"peggedUSD": size - week / 7},
            "circulatingPrevWeek": {"peggedUSD": size - week},
            "circulatingPrevMonth": {"peggedUSD": size - week * 3},
            "price": price,
        })
    return {"peggedAssets": assets}


def supply_chart_raw(days: int = 400, seed: int = 7) -> list:
    rng = random.Random(seed)
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=days)
    total, rows = 236e9, []
    for d in range(days + 1):
        total *= 1 + 0.0007 + rng.gauss(0, 0.0012)
        ts = int((start + timedelta(days=d)).timestamp())
        rows.append({"date": str(ts), "totalCirculatingUSD": {"peggedUSD": total * 0.985,
                                                             "peggedEUR": total * 0.015}})
    return rows


def markets_raw() -> list:
    return [{
        "question": "Demo market: will the sample event happen by December 31?",
        "slug": "demo-sample-event",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.63", "0.37"]',
        "clobTokenIds": '["111", "222"]',
        "volume24hr": 4_820_000,
        "endDate": "2026-12-31T00:00:00Z",
    }]


def history_raw(seed: int = 7) -> dict:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    p, rows = 0.41, []
    for i in range(30 * 8):
        t = now - timedelta(hours=3 * (30 * 8 - i))
        p += 0.0009 + rng.gauss(0, 0.012) + 0.004 * math.sin(i / 20)
        p = min(max(p, 0.03), 0.97)
        rows.append({"t": int(t.timestamp()), "p": round(p, 3)})
    return {"history": rows}
