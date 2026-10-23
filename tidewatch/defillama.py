"""Stablecoin data from DefiLlama's free stablecoins API (no key needed).

Endpoints:
  https://stablecoins.llama.fi/stablecoins?includePrices=true
  https://stablecoins.llama.fi/stablecoincharts/all
  https://stablecoins.llama.fi/stablecoinprices
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # chain -> (supply now, supply a month ago) for this coin, from chainCirculating
    chains: dict[str, tuple[float, float]] = field(default_factory=dict, compare=False, repr=False)

    @property
    def change_7d(self) -> float | None:
        return None if self.prev_week is None else self.circulating - self.prev_week


@dataclass(frozen=True)
class SupplyPoint:
    day: date
    total_usd: float
    pegged_usd: float = 0.0  # dollar-pegged coins only; total_usd also counts EUR, gold...


def _peg_amount(block: Any, peg_type: str) -> float | None:
    if not isinstance(block, dict):
        return None
    value = block.get(peg_type)
    return float(value) if isinstance(value, (int, float)) else None


def _chain_split(block: Any, peg: str) -> dict[str, tuple[float, float]]:
    out = {}
    for chain, v in (block or {}).items() if isinstance(block, dict) else []:
        if not isinstance(v, dict):
            continue
        now = _peg_amount(v.get("current"), peg)
        if now:
            out[chain] = (now, _peg_amount(v.get("circulatingPrevMonth"), peg) or 0.0)
    return out


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
                chains=_chain_split(a.get("chainCirculating"), peg),
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
            pegged = usd.get("peggedUSD")
            points.append(SupplyPoint(day=day, total_usd=total,
                                      pegged_usd=float(pegged) if isinstance(pegged, (int, float)) else 0.0))
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


# Supply by chain and per coin ----------------------------------------------------

CHAIN_WINDOW_DAYS = 30
SHARE_WINDOW_DAYS = 180
USDT_ID, USDC_ID = 1, 2  # DefiLlama stablecoin ids


@dataclass(frozen=True)
class ChainSupply:
    name: str
    now: float          # USD-pegged stablecoins on the chain today
    before: float       # the same, a month ago

    @property
    def change(self) -> float:
        return self.now - self.before

    @property
    def change_pct(self) -> float:
        return self.change / self.before if self.before else 0.0


def chain_supplies(coins: list[Stablecoin], *, top: int = 8) -> list[ChainSupply]:
    """Supply per chain, summed over USD stablecoins, from the same /stablecoins
    snapshot the movers chart uses. Pass the coins already filtered for frozen data.

    (Per-chain history endpoints give nearly the same totals but cost nine slow
    calls, about 40 seconds and more when DefiLlama is busy.)"""
    now: dict[str, float] = {}
    before: dict[str, float] = {}
    for c in coins:
        if c.peg_type != "peggedUSD":
            continue
        for chain, (n, b) in c.chains.items():
            now[chain] = now.get(chain, 0.0) + n
            before[chain] = before.get(chain, 0.0) + b
    biggest = sorted(now, key=now.get, reverse=True)[:top]
    return [ChainSupply(name=ch, now=now[ch], before=before[ch]) for ch in biggest if before[ch] > 0]


def _trim_rows(raw: Any, keep: int) -> Any:
    """Trailing daily rows of a /stablecoincharts response, kept whole."""
    if not isinstance(raw, list):
        return raw
    return sorted((r for r in raw if isinstance(r, dict)), key=_row_ts)[-keep:]


def parse_pegged_history(raw: Any) -> list[tuple[date, float]]:
    """(day, USD-pegged supply) rows from any /stablecoincharts response."""
    out = []
    for r in raw if isinstance(raw, list) else []:
        amount = _peg_amount(r.get("totalCirculatingUSD"), "peggedUSD") if isinstance(r, dict) else None
        ts = _row_ts(r)
        if amount and ts:
            out.append((datetime.fromtimestamp(ts, tz=timezone.utc).date(), amount))
    out.sort()
    return out


def fetch_coin_history(fetcher: Fetcher, coin_id: int, *,
                       days: int = SHARE_WINDOW_DAYS) -> list[tuple[date, float]]:
    raw = fetcher.get_json(f"{BASE}/stablecoincharts/all", {"stablecoin": coin_id},
                           name=f"defillama_coin_{coin_id}", trim=lambda r: _trim_rows(r, days + 10))
    return parse_pegged_history(raw)
