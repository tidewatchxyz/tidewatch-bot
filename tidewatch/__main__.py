"""Command line entry point.

  python -m tidewatch daily                   all charts + captions for today
  python -m tidewatch daily --market SLUG     pick the Polymarket market for the odds chart
  python -m tidewatch daily --from-raw DIR    re-render a past run from its saved raw/ data
  python -m tidewatch daily --sample          offline run on fake data (stamped, never post)
  python -m tidewatch markets                 list today's most traded Polymarket markets
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import charts, defillama, polymarket, sample
from .fmt import usd
from .http import Fetcher


SWING_MIN_WEEK_VOLUME = 100e3  # a market needs this much weekly trading to count as a swing


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@dataclass
class Data:
    coins: list
    points: list
    ranges: dict = field(default_factory=dict)
    market: object = None
    history: list = field(default_factory=list)
    markets: list = field(default_factory=list)   # top-volume plus crypto/economy topic markets
    chains: list = field(default_factory=list)
    usdt: list = field(default_factory=list)
    usdc: list = field(default_factory=list)


def _optional(future, label: str, default):
    """Result of a fetch that only some charts need; a failure skips those charts."""
    try:
        return future.result()
    except Exception as exc:  # noqa: BLE001  the other charts can still go out
        print(f"{label} unavailable: {exc}", file=sys.stderr)
        return default


def _load(args, fetcher: Fetcher) -> Data:
    """Every input the charts need, fetched in parallel."""
    if args.sample:
        coins = defillama.parse_stablecoins(sample.stablecoins_raw())
        markets = polymarket.parse_events(sample.topic_events_raw())
        return Data(
            coins=coins,
            points=defillama.parse_supply_chart(sample.supply_chart_raw()),
            ranges=defillama.parse_peg_ranges(sample.price_history_raw(), [c.gecko_id for c in coins]),
            market=polymarket.parse_market(sample.markets_raw()[0]),
            history=polymarket.parse_history(sample.history_raw()),
            markets=markets,
            chains=[cs for name, _ in defillama.parse_chain_list(sample.chain_list_raw(), 8)
                    if (cs := defillama.chain_supply(name, defillama.parse_pegged_history(
                        sample.chain_history_raw(name))))],
            usdt=defillama.parse_pegged_history(sample.coin_history_raw(defillama.USDT_ID)),
            usdc=defillama.parse_pegged_history(sample.coin_history_raw(defillama.USDC_ID)),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        coins_f = pool.submit(defillama.fetch_stablecoins, fetcher)
        points_f = pool.submit(defillama.fetch_supply_chart, fetcher)
        prices_f = pool.submit(defillama.fetch_price_history, fetcher)
        top_f = pool.submit(polymarket.fetch_top_markets, fetcher)
        topic_f = pool.submit(polymarket.fetch_topic_markets, fetcher)
        chains_f = pool.submit(defillama.fetch_chains, fetcher)
        usdt_f = pool.submit(defillama.fetch_coin_history, fetcher, defillama.USDT_ID)
        usdc_f = pool.submit(defillama.fetch_coin_history, fetcher, defillama.USDC_ID)

        top = _optional(top_f, "Polymarket top markets", [])
        topic = _optional(topic_f, "Polymarket topic markets", [])
        markets = polymarket.merge_markets(top, topic)
        if args.market:
            market = _optional(pool.submit(polymarket.fetch_market, fetcher, args.market),
                               f"Market '{args.market}'", None)
        else:
            market = polymarket.pick_market(markets)
        history = []
        if market and market.token_ids:
            history = _optional(pool.submit(polymarket.fetch_history, fetcher, market.token_ids[0]),
                                "Odds history", [])
        coins = coins_f.result()
        try:
            ranges = defillama.parse_peg_ranges(prices_f.result(), [c.gecko_id for c in coins])
        except Exception as exc:  # noqa: BLE001  peg chart still works without history
            print(f"Peg history unavailable: {exc}", file=sys.stderr)
            ranges = {}
        return Data(coins=coins, points=points_f.result(), ranges=ranges, market=market,
                    history=history, markets=markets,
                    chains=_optional(chains_f, "Chain supply", []),
                    usdt=_optional(usdt_f, "USDT history", []),
                    usdc=_optional(usdc_f, "USDC history", []))


# When two charts score the same, the earlier one here wins.
PRIORITY = ("pegs", "fed", "movers", "swings", "chains", "odds", "share", "supply")


def rank(posts: list) -> list:
    return sorted(posts, key=lambda p: (-p.score, PRIORITY.index(p.name) if p.name in PRIORITY else 99))


def cmd_daily(args) -> int:
    started = time.perf_counter()
    if args.from_raw:
        raw = Path(args.from_raw)
        raw = raw / "raw" if (raw / "raw").is_dir() else raw
        out = raw.parent / "rerender"
        fetcher = Fetcher(replay_dir=raw)
    else:
        day = _today()
        out = Path(args.out) / (f"{day}-sample" if args.sample else day)
        fetcher = Fetcher(raw_dir=out / "raw")

    try:
        d = _load(args, fetcher)
    except Exception as exc:  # noqa: BLE001
        print(f"Data fetch failed: {exc}", file=sys.stderr)
        return 1
    fetched = time.perf_counter()

    today = d.points[-1].day if d.points else datetime.now(timezone.utc).date()
    s = args.sample
    jobs = [
        ("supply", lambda: charts.supply_chart(d.points, out, sample=s)),
        ("movers", lambda: charts.movers_chart(d.coins, out, today, sample=s)),
        ("pegs", lambda: charts.peg_chart(d.coins, out, today, ranges=d.ranges, sample=s)),
    ]
    if d.market and d.market.is_binary_yes_no and len(d.history) >= 2:
        jobs.append(("odds", lambda: charts.odds_chart(d.market, d.history, out, sample=s)))
    else:
        print("Skipping odds chart: no chart-worthy market or no price history", file=sys.stderr)
    if d.chains:
        jobs.append(("chains", lambda: charts.chains_chart(d.chains, out, today, sample=s)))
    if d.usdt and d.usdc:
        jobs.append(("share", lambda: charts.share_chart(d.points, d.usdt, d.usdc, out, sample=s)))
    if d.markets:
        jobs.append(("swings", lambda: charts.swings_chart(
            polymarket.swing_candidates(d.markets, min_week_volume=SWING_MIN_WEEK_VOLUME),
            out, today, sample=s)))
        jobs.append(("fed", lambda: charts.fed_chart(polymarket.fed_meeting(d.markets), out, today,
                                                     sample=s)))

    posts, failed = [], 0
    for name, job in jobs:
        try:
            posts.append(job())
            print(f"  ok   {name:7} {posts[-1].headline}")
        except charts.NoStory as why:  # not enough data for this chart today; not a failure
            print(f"  skip {name:7} {why}")
        except Exception:  # noqa: BLE001  one bad chart should not kill the rest
            failed += 1
            print(f"  FAIL {name}", file=sys.stderr)
            traceback.print_exc()

    ranked = rank(posts)
    md = [f"# Tidewatch drafts for {today.isoformat()}\n"]
    if args.sample:
        md.append("**SAMPLE DATA. These numbers are fake. Do not post.**\n")
    if ranked:
        md += [f"Strongest story today: **{ranked[0].name}**, {ranked[0].headline}\n",
               "| Rank | Chart | Score | Why |", "|---|---|---|---|"]
        md += [f"| {i} | {p.name} | {p.score:.0f} | {p.why} |" for i, p in enumerate(ranked, 1)]
        md.append("")
    for p in ranked:
        md += [f"## {p.name}: {p.headline}\n", f"Image: `{p.image.name}`\n", "Caption:\n",
               "```", p.caption, "```\n", "Alt text (paste into the image description):\n",
               "```", p.alt_text, "```\n"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "captions.md").write_text("\n".join(md))
    done = time.perf_counter()
    if ranked:
        print("\nStrongest story today:")
        for i, p in enumerate(ranked, 1):
            print(f"  {i}. {p.name:7} {p.score:5.1f}  {p.why}")
    print(f"\nWrote {len(posts)} chart(s) and captions.md to {out} "
          f"(data {fetched - started:.1f}s, charts {done - fetched:.1f}s)")
    return 1 if failed else 0


def cmd_markets(args) -> int:
    for m in polymarket.fetch_top_markets(Fetcher(), limit=args.limit):
        odds = f"{m.yes_price * 100:4.0f}%" if m.yes_price is not None else "  n/a"
        print(f"{usd(m.volume_24h):>8}  {odds}  {m.slug}\n                 {m.question}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidewatch", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("daily", help="render today's charts and caption drafts")
    d.add_argument("--out", default="out", help="output folder (default: out)")
    d.add_argument("--market", help="Polymarket market slug for the odds chart")
    src = d.add_mutually_exclusive_group()
    src.add_argument("--sample", action="store_true", help="use fake offline data")
    src.add_argument("--from-raw", metavar="DIR", help="re-render from a past run's raw/ folder")
    d.set_defaults(func=cmd_daily)

    m = sub.add_parser("markets", help="list the most traded Polymarket markets")
    m.add_argument("--limit", type=int, default=15)
    m.set_defaults(func=cmd_markets)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
