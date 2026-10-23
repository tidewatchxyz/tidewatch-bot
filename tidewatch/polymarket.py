"""Prediction market data from Polymarket's public read APIs (no key needed).

Endpoints:
  https://gamma-api.polymarket.com/markets         market metadata and current odds
  https://clob.polymarket.com/prices-history        price history for one outcome token
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    volume_1wk: float = 0.0
    is_sports: bool = False
    one_day_change: float | None = None   # Yes price change, 0..1 scale
    one_week_change: float | None = None
    event_id: str = ""
    event_title: str = ""
    group_title: str = ""                 # outcome label inside a multi-market event

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
    def num(*keys) -> float:
        for k in keys:
            try:
                return float(m.get(k) or 0)
            except (TypeError, ValueError):
                continue
        return 0.0

    def change(key: str) -> float | None:
        try:
            return None if m.get(key) is None else float(m[key])
        except (TypeError, ValueError):
            return None

    events = m.get("events") if isinstance(m.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}

    return Market(
        question=m["question"].strip(),
        slug=m.get("slug", ""),
        outcomes=outcomes,
        prices=prices,
        token_ids=tokens,
        volume_24h=num("volume24hr", "volume24hrClob"),
        end_date=m.get("endDate"),
        volume_1wk=num("volume1wk", "volume1wkClob"),
        is_sports=bool(m.get("sportsMarketType") or m.get("gameStartTime")),
        one_day_change=change("oneDayPriceChange"),
        one_week_change=change("oneWeekPriceChange"),
        event_id=str(event.get("id") or ""),
        event_title=str(event.get("title") or ""),
        group_title=str(m.get("groupItemTitle") or ""),
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


CRYPTO_MACRO = re.compile(
    r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|crypto|stablecoins?|tether|usdt|usdc|circle|"
    r"coinbase|binance|etf|fed|fomc|interest rates?|rate cuts?|inflation|cpi|recession|"
    r"tariffs?|sec|treasury|token|airdrop|memecoin|microstrategy|strategy)\b", re.I)


def _end(m: Market) -> datetime | None:
    if not m.end_date:
        return None
    try:
        return datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
    except ValueError:
        return None


def chart_worthy(m: Market, now: datetime | None = None) -> bool:
    """A market worth a 30-day odds chart: binary, not sports, live for another
    week, and genuinely uncertain (8% to 92%)."""
    now = now or datetime.now(timezone.utc)
    end = _end(m)
    return (m.is_binary_yes_no and not m.is_sports and m.yes_price is not None
            and 0.08 <= m.yes_price <= 0.92
            and (end is None or end - now >= timedelta(days=7)))


def pick_market(markets: list[Market], now: datetime | None = None) -> Market | None:
    """Best chart candidate: most weekly volume, crypto/macro questions boosted 5x."""
    ok = [m for m in markets if chart_worthy(m, now)]
    score = lambda m: (m.volume_1wk or m.volume_24h) * (5 if CRYPTO_MACRO.search(m.question) else 1)
    return max(ok, key=score, default=None)


# Markets whose odds can be read as news rather than a scoreboard.
_NOT_NEWS = re.compile(r"\bup or down\b|\bO/U\b|\bvs\.?\b|\bwin on \d{4}-|\bleading at halftime\b",
                       re.I)


def swing_pool(markets: list[Market], *, min_week_volume: float = 250e3) -> list[Market]:
    """Liquid crypto and macro markets that could be a swing, before knowing how far
    each one moved: no sports, no daily up/down bets, not about to settle."""
    return [m for m in markets
            if m.yes_price is not None and not m.is_sports and CRYPTO_MACRO.search(m.question)
            and not _NOT_NEWS.search(m.question) and 0.04 <= m.yes_price <= 0.96
            and (m.volume_1wk or 0) >= min_week_volume]


def swing_candidates(markets: list[Market], *, min_week_volume: float = 250e3) -> list[Market]:
    """Crypto and macro markets whose weekly odds move means something.

    Sports, daily up/down markets, anything thin, and anything already within 4
    points of settling are left out: a game ending at 100% is a result, not a swing.
    One market per event, the one that moved most, so a single Fed meeting cannot
    fill the chart with its five outcomes.
    """
    best: dict[str, Market] = {}
    for m in swing_pool(markets, min_week_volume=min_week_volume):
        if m.one_week_change is None:
            continue
        key = m.event_id or m.slug
        if key not in best or abs(m.one_week_change) > abs(best[key].one_week_change or 0):
            best[key] = m
    return sorted(best.values(), key=lambda m: abs(m.one_week_change or 0), reverse=True)


_FED_EVENT = re.compile(r"\bfed decision\b", re.I)
_FED_MONTH = re.compile(r"after the (\w+) \d{4} meeting", re.I)


def fed_meeting(markets: list[Market], now: datetime | None = None) -> list[Market]:
    """All outcome markets for the next Fed meeting Polymarket is still trading."""
    now = now or datetime.now(timezone.utc)
    events: dict[str, list[Market]] = {}
    for m in markets:
        if m.yes_price is None or not m.event_id:
            continue
        if _FED_EVENT.search(m.event_title) or (_FED_MONTH.search(m.question) and "Fed" in m.question):
            events.setdefault(m.event_id, []).append(m)
    live = [(min((_end(m) for m in ms if _end(m)), default=None), ms)
            for ms in events.values() if len(ms) >= 2]
    live = [(end, ms) for end, ms in live if end is None or end > now]
    if not live:
        return []
    live.sort(key=lambda t: t[0] or datetime.max.replace(tzinfo=timezone.utc))
    return live[0][1]


def fed_month(markets: list[Market]) -> str | None:
    for m in markets:
        hit = _FED_MONTH.search(m.question)
        if hit:
            return hit.group(1).capitalize()
    return None


def fetch_top_markets(fetcher: Fetcher, limit: int = 100) -> list[Market]:
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


TOPIC_TAGS = ("crypto", "economy")
_KEEP_WEEK_VOLUME = 20e3


def trim_events(raw: Any) -> list:
    """Keep only markets that traded in the last week. Each topic feed is 2 to 4 MB,
    nearly all of it dormant strike ladders; market objects are kept whole."""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        live = []
        for m in e.get("markets") or []:
            try:
                if float(m.get("volume1wk") or 0) >= _KEEP_WEEK_VOLUME:
                    live.append(m)
            except (AttributeError, TypeError, ValueError):
                continue
        if live:
            out.append({**e, "markets": live})
    return out


def parse_events(raw: Any) -> list[Market]:
    """Markets from an /events response. Event fields are copied onto each market,
    which is how /markets reports them too."""
    found = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        ref = {"id": e.get("id"), "title": e.get("title"), "slug": e.get("slug")}
        for m in e.get("markets") or []:
            if isinstance(m, dict):
                parsed = parse_market({**m, "events": m.get("events") or [ref]})
                if parsed is not None:
                    found.append(parsed)
    return found


def fetch_topic_markets(fetcher: Fetcher, tags: tuple[str, ...] = TOPIC_TAGS) -> list[Market]:
    """Liquid markets under Polymarket's topic tags. The plain top-volume list is
    mostly sports, so crypto and macro questions rarely make it in."""
    found: dict[str, Market] = {}
    for tag in tags:
        raw = fetcher.get_json(
            f"{GAMMA}/events",
            {"tag_slug": tag, "active": "true", "closed": "false", "order": "volume1wk",
             "ascending": "false", "limit": 50},
            name=f"polymarket_events_{tag}", trim=trim_events)
        for m in parse_events(raw):
            found.setdefault(m.slug or m.question, m)
    return list(found.values())


def merge_markets(*lists: list[Market]) -> list[Market]:
    seen: dict[str, Market] = {}
    for ms in lists:
        for m in ms:
            seen.setdefault(m.slug or m.question, m)
    return list(seen.values())


# Week-ago odds ------------------------------------------------------------------
#
# Gamma's oneWeekPriceChange is not reliable. On Oct 21, 2026 it put the October
# Fed "no change" market at +27.5 points on the week (from 61%); the CLOB price
# history shows 80.5% a week earlier and never below 70.5% all week. A draft built
# on it said "rose to 88% from 60%". So the week-ago price comes from the history.

def week_ago_price(history: list[PricePoint], days: int = 7) -> float | None:
    """The last traded price at or before `days` before the latest point, or None
    when the history does not reach back that far."""
    if not history:
        return None
    target = history[-1].at - timedelta(days=days)
    before = [p for p in history if p.at <= target]
    return before[-1].price if before else None


def trim_history(raw: Any, days: int = 9) -> Any:
    rows = raw.get("history") if isinstance(raw, dict) else None
    if not isinstance(rows, list) or not rows:
        return raw
    try:
        cutoff = max(int(r["t"]) for r in rows) - days * 86400
        return {**raw, "history": [r for r in rows if int(r["t"]) >= cutoff]}
    except (KeyError, TypeError, ValueError):
        return raw


def with_true_week_change(fetcher: Fetcher, markets: list[Market], days: int = 7) -> list[Market]:
    """The same markets with one_week_change recomputed from price history. A market
    whose history cannot be fetched, or does not go back far enough, gets None, so
    it drops out of any chart that needs the weekly move rather than being guessed."""
    def one(m: Market) -> Market:
        try:
            raw = fetcher.get_json(
                f"{CLOB}/prices-history",
                {"market": m.token_ids[0], "interval": "1m", "fidelity": 360},
                name=f"polymarket_week_{m.token_ids[0][:16]}",
                trim=lambda r: trim_history(r, days + 2))
            then = week_ago_price(parse_history(raw), days)
        except Exception:  # noqa: BLE001  one missing history drops one market
            then = None
        change = None if then is None or m.yes_price is None else m.yes_price - then
        return dataclasses.replace(m, one_week_change=change)

    todo = [m for m in markets if m.token_ids]
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, todo))


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
        name=f"polymarket_history_{token_id[:16]}",
    )
    pts = parse_history(raw)
    if len(pts) < 2:  # fall back to an explicit time window
        now = int(time.time())
        raw = fetcher.get_json(
            f"{CLOB}/prices-history",
            {"market": token_id, "startTs": now - days * 86400, "endTs": now, "fidelity": 180},
            name=f"polymarket_history_window_{token_id[:16]}",
        )
        pts = parse_history(raw)
    return pts
