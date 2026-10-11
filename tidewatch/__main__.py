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
from datetime import datetime, timezone
from pathlib import Path

from . import charts, defillama, polymarket, sample
from .fmt import usd
from .http import Fetcher


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _pick_market(fetcher: Fetcher, slug: str | None):
    if slug:
        return polymarket.fetch_market(fetcher, slug)
    return polymarket.pick_market(polymarket.fetch_top_markets(fetcher))


def _load(args, fetcher: Fetcher):
    """Return (stablecoins, supply points, market, history), fetching in parallel."""
    if args.sample:
        return (defillama.parse_stablecoins(sample.stablecoins_raw()),
                defillama.parse_supply_chart(sample.supply_chart_raw()),
                polymarket.parse_market(sample.markets_raw()[0]),
                polymarket.parse_history(sample.history_raw()))

    with ThreadPoolExecutor(max_workers=3) as pool:
        coins_f = pool.submit(defillama.fetch_stablecoins, fetcher)
        points_f = pool.submit(defillama.fetch_supply_chart, fetcher)
        market_f = pool.submit(_pick_market, fetcher, args.market)
        market = market_f.result()
        # History depends on the market, so it starts as soon as the market is known.
        history_f = pool.submit(polymarket.fetch_history, fetcher, market.token_ids[0]) \
            if market and market.token_ids else None
        history = []
        if history_f:
            try:
                history = history_f.result()
            except Exception as exc:  # noqa: BLE001  the stablecoin charts can still go out
                print(f"Odds history unavailable: {exc}", file=sys.stderr)
        return coins_f.result(), points_f.result(), market, history


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
        coins, points, market, history = _load(args, fetcher)
    except Exception as exc:  # noqa: BLE001
        print(f"Data fetch failed: {exc}", file=sys.stderr)
        return 1
    fetched = time.perf_counter()

    today = points[-1].day if points else datetime.now(timezone.utc).date()
    jobs = [
        ("supply", lambda: charts.supply_chart(points, out, sample=args.sample)),
        ("movers", lambda: charts.movers_chart(coins, out, today, sample=args.sample)),
        ("pegs", lambda: charts.peg_chart(coins, out, today, sample=args.sample)),
    ]
    if market and market.is_binary_yes_no and len(history) >= 2:
        jobs.append(("odds", lambda: charts.odds_chart(market, history, out, sample=args.sample)))
    else:
        print("Skipping odds chart: no chart-worthy market or no price history", file=sys.stderr)

    posts, failed = [], 0
    for name, job in jobs:
        try:
            posts.append(job())
            print(f"  ok   {name:7} {posts[-1].headline}")
        except Exception:  # noqa: BLE001  one bad chart should not kill the rest
            failed += 1
            print(f"  FAIL {name}", file=sys.stderr)
            traceback.print_exc()

    md = [f"# Tidewatch drafts for {today.isoformat()}\n"]
    if args.sample:
        md.append("**SAMPLE DATA. These numbers are fake. Do not post.**\n")
    for p in posts:
        md += [f"## {p.name}: {p.headline}\n", f"Image: `{p.image.name}`\n", "Caption:\n",
               "```", p.caption, "```\n", "Alt text (paste into the image description):\n",
               "```", p.alt_text, "```\n"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "captions.md").write_text("\n".join(md))
    done = time.perf_counter()
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
