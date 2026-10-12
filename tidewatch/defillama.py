"""Stablecoin data from DefiLlama's free stablecoins API (no key needed).

Endpoints:
  https://stablecoins.llama.fi/stablecoins?includePrices=true
  https://stablecoins.llama.fi/stablecoincharts/all
  https://stablecoins.llama.fi/stablecoinprices
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from .http import Fetcher

BASE = "https://stablecoins.llama.fi"
PEG_WINDOW_DAYS = 30  # how far back the peg chart looks


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
    yield_bearing: bool = False
    gecko_id: str = ""

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
                yield_bearing=a.get("yieldBearing") is True,
                gecko_id=a.get("gecko_id") or "",
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


# Peg history ------------------------------------------------------------------

@dataclass(frozen=True)
class PegRange:
    """How far a coin strayed from $1 over the window, in basis points."""
    low_bps: float
    high_bps: float
    days: int  # daily closes actually seen (a new coin may have fewer)

    @property
    def width_bps(self) -> float:
        return self.high_bps - self.low_bps

    @property
    def worst_bps(self) -> float:
        """The end of the range furthest from $1, keeping its sign."""
        return self.low_bps if abs(self.low_bps) >= abs(self.high_bps) else self.high_bps


def _row_ts(row: Any) -> int:
    try:
        return int(row.get("date"))
    except (AttributeError, TypeError, ValueError):
        return 0


def trim_price_history(raw: Any, *, days: int = PEG_WINDOW_DAYS) -> list:
    """Keep only the trailing `days` daily rows.

    The full response is ~6 MB, nearly all of it history no chart reads. Rows are
    kept whole, so the archived file still shows every price behind the chart.
    """
    if not isinstance(raw, list):
        return raw
    rows = sorted((r for r in raw if isinstance(r, dict) and isinstance(r.get("prices"), dict)),
                  key=_row_ts)
    return rows[-days:]


def parse_peg_ranges(raw: Any, gecko_ids: Iterable[str], *,
                     days: int = PEG_WINDOW_DAYS) -> dict[str, PegRange]:
    """Low/high deviation from $1 per coin over the trailing window, keyed by gecko id."""
    wanted = {g for g in gecko_ids if g}
    if not wanted or not isinstance(raw, list):
        return {}
    seen: dict[str, list[float]] = {}
    for row in trim_price_history(raw, days=days):
        for gid, price in row["prices"].items():
            if gid in wanted and isinstance(price, (int, float)) and 0 < price < 100:
                seen.setdefault(gid, []).append(float(price))
    return {gid: PegRange(low_bps=(min(prices) - 1) * 10_000,
                          high_bps=(max(prices) - 1) * 10_000,
                          days=len(prices))
            for gid, prices in seen.items() if prices}


def fetch_price_history(fetcher: Fetcher, *, days: int = PEG_WINDOW_DAYS) -> list:
    return fetcher.get_json(f"{BASE}/stablecoinprices", name="defillama_stablecoin_prices",
                            trim=lambda raw: trim_price_history(raw, days=days))
