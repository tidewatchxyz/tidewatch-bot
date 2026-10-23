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
            "gecko_id": f"demo-dollar-{chr(97 + i)}",
            "pegType": "peggedUSD",
            "pegMechanism": "fiat-backed",
            "circulating": {"peggedUSD": size},
            "circulatingPrevDay": {"peggedUSD": size - week / 7},
            "circulatingPrevWeek": {"peggedUSD": size - week},
            "circulatingPrevMonth": {"peggedUSD": size - week * 3},
            "price": price,
            "chainCirculating": {  # spread over five demo chains, each with its own drift
                f"Demo Chain {chr(65 + j)}": {
                    "current": {"peggedUSD": size * w},
                    "circulatingPrevMonth": {"peggedUSD": size * w * (1 - d)}}
                for j, (w, d) in enumerate([(0.45, -0.012), (0.3, 0.018), (0.12, 0.03),
                                             (0.08, -0.025), (0.05, 0.06)])},
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


def price_history_raw(days: int = 30, coins: int = 12, seed: int = 11) -> list:
    """Daily prices in the /stablecoinprices shape, for the peg chart's range bars."""
    rng = random.Random(seed)
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start -= timedelta(days=days - 1)
    rows = []
    for d in range(days):
        prices = {}
        for i in range(coins):
            gid = f"demo-dollar-{chr(97 + i)}"
            wobble = rng.gauss(0, 0.0004 + 0.0002 * (i % 4))
            if i == 7:  # the deliberately off-peg coin sags over the window
                wobble -= 0.0075 * min(1.0, d / max(days - 8, 1))
            prices[gid] = round(1 + wobble, 6)
        rows.append({"date": str(int((start + timedelta(days=d)).timestamp())),
                     "prices": prices})
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


def coin_history_raw(coin_id: int, days: int = 410) -> list:
    """USDT-like (1) and USDC-like (2) supply, sized against supply_chart_raw."""
    total = {r["date"]: sum(r["totalCirculatingUSD"].values()) * 0.985
             for r in supply_chart_raw(days=days)}
    share = {1: (0.60, 0.0002), 2: (0.25, -0.0004)}.get(coin_id, (0.05, 0.0))
    rng = random.Random(70 + coin_id)
    rows, s = [], share[0]
    for ts, t in total.items():
        s = max(0.01, s + share[1] / 10 + rng.gauss(0, 0.0004))
        rows.append({"date": ts, "totalCirculatingUSD": {"peggedUSD": t * s}})
    return rows


def topic_events_raw() -> list:
    """Polymarket /events shape: a few crypto markets that moved, plus a Fed meeting."""
    end = (datetime.now(timezone.utc) + timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def mk(q, p, wk, vol, group=""):
        return {"question": q, "slug": q.lower().replace(" ", "-")[:60], "outcomes": '["Yes", "No"]',
                "outcomePrices": f'["{p}", "{round(1 - p, 4)}"]', "clobTokenIds": '["1", "2"]',
                "volume24hr": vol / 5, "volume1wk": vol, "oneWeekPriceChange": wk,
                "oneDayPriceChange": wk / 3, "endDate": end, "groupItemTitle": group}

    crypto = [mk("Demo: Will Bitcoin reach $150,000 by December 31?", 0.24, -0.27, 2.1e6),
              mk("Demo: Will Ethereum dip to $1,500 by December 31?", 0.31, 0.12, 900e3),
              mk("Demo: Will Solana reach $400 in 2026?", 0.12, -0.08, 400e3),
              mk("Demo: Will a spot XRP ETF launch this year?", 0.58, 0.06, 650e3)]
    fed = [mk("Demo: Will the Fed increase interest rates by 25 bps after the December 2026 meeting?",
              0.22, 0.09, 3e6, "25 bps increase"),
           mk("Demo: Will there be no change in Fed interest rates after the December 2026 meeting?",
              0.74, -0.08, 4e6, "No change"),
           mk("Demo: Will the Fed decrease interest rates by 25 bps after the December 2026 meeting?",
              0.04, -0.01, 1e6, "25 bps decrease")]
    events = [{"id": f"demo-{i}", "title": m["question"], "slug": f"demo-{i}", "markets": [m]}
              for i, m in enumerate(crypto)]
    events.append({"id": "demo-fed", "title": "Demo: Fed Decision in December?", "slug": "demo-fed",
                   "markets": fed})
    return events
