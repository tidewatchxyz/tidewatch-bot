"""Stablecoin data from DefiLlama's free stablecoins API (no key needed).

Endpoints:
  https://stablecoins.llama.fi/stablecoins?includePrices=true
  https://stablecoins.llama.fi/stablecoincharts/all
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from .http import Fetcher

BASE = "https://stablecoins.llama.fi"


@dataclass(frozen=True)
class Stablecoin:
    name: str
    symbol: str
    peg_type: str
    mechanism: str
    circulating: float
    prev_day: float | None
    prev_week: float | None
    prev_month: float | None
    price: float | None

    @property
    def change_7d(self) -> float | None:
        return None if self.prev_week is None else self.circulating - self.prev_week


@dataclass(frozen=True)
class SupplyPoint:
    day: date
    total_usd: float


def _peg_amount(block: Any, peg_type: str) -> float | None:
    if not isinstance(block, dict):
        return None
    value = block.get(peg_type)
    return float(value) if isinstance(value, (int, float)) else None


def parse_stablecoins(raw: dict) -> list[Stablecoin]:
    out = []
    for a in raw.get("peggedAssets", []):
        peg = a.get("pegType") or "peggedUSD"
        circ = _peg_amount(a.get("circulating"), peg)
        if circ is None or circ <= 0:
            continue
        price = a.get("price")
        out.append(
            Stablecoin(
                name=a.get("name", "?"),
                symbol=a.get("symbol", "?"),
                peg_type=peg,
                mechanism=a.get("pegMechanism", ""),
                circulating=circ,
                prev_day=_peg_amount(a.get("circulatingPrevDay"), peg),
                prev_week=_peg_amount(a.get("circulatingPrevWeek"), peg),
                prev_month=_peg_amount(a.get("circulatingPrevMonth"), peg),
                price=float(price) if isinstance(price, (int, float)) else None,
            )
        )
    out.sort(key=lambda s: s.circulating, reverse=True)
    return out


def parse_supply_chart(raw: list) -> list[SupplyPoint]:
    points = []
    for row in raw:
        try:
            ts = int(row["date"])
        except (KeyError, TypeError, ValueError):
            continue
        usd = row.get("totalCirculatingUSD") or {}
        total = sum(v for v in usd.values() if isinstance(v, (int, float)))
        if total > 0:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            points.append(SupplyPoint(day=day, total_usd=total))
    points.sort(key=lambda p: p.day)
    return points


def fetch_stablecoins(fetcher: Fetcher) -> list[Stablecoin]:
    raw = fetcher.get_json(f"{BASE}/stablecoins", {"includePrices": "true"},
                           name="defillama_stablecoins")
    return parse_stablecoins(raw)


def fetch_supply_chart(fetcher: Fetcher) -> list[SupplyPoint]:
    raw = fetcher.get_json(f"{BASE}/stablecoincharts/all", name="defillama_supply_chart")
    return parse_supply_chart(raw)
