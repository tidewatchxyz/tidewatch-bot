"""The daily Tidewatch charts. Each builder saves a PNG and returns a Post.

Every chart leads with a headline that states the finding (computed from the
data), backs it with one hero number, and annotates at most one point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator, MultipleLocator

from . import fmt
from .defillama import PEG_WINDOW_DAYS, PegRange, Stablecoin, SupplyPoint
from .polymarket import Market, PricePoint
from .style import (BAND, BG, BRAND, DOWN, GRID, LINE_W, NEUTRAL, RANGE, TEXT, TEXT_2, TEXT_3,
                    UP, WARN, end_dot, label_margin, new_frame, rounded_hbars, save)

PEG_BAND_BPS = 50
MIN_PEG_SPAN_BPS = 15.0  # never zoom in tighter than this, or noise looks like news
LABEL_ROOM = 0.52  # bar-span fraction kept free for the value labels
MONO_CHAR_PT = 5.2  # width of one IBM Plex Mono character at ~8.6pt


@dataclass
class Post:
    name: str
    image: Path
    headline: str
    caption: str
    alt_text: str


def _date_label(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _short_date(d: date) -> str:
    return f"{d:%b} {d.day}"


def _labels(coins: list[Stablecoin], universe: list[Stablecoin]) -> dict[int, str]:
    """Symbol per coin, or a short name when two coins share a symbol (e.g. two OUSDs)."""
    counts: dict[str, int] = {}
    for c in universe:
        counts[c.symbol] = counts.get(c.symbol, 0) + 1
    out = {}
    for c in coins:
        if counts.get(c.symbol, 0) > 1:
            name = c.name if len(c.name) <= 14 else c.name[:13].rstrip() + "."
            out[id(c)] = name
        else:
            out[id(c)] = c.symbol
    return out


def is_dollar_pegged(c: Stablecoin) -> bool:
    """True for coins meant to trade at $1. Yield-bearing tokens (USYC, USDY...) accrue
    value above $1 by design, so they would show up as fake 'off peg' alarms."""
    if c.peg_type != "peggedUSD" or c.price is None or c.yield_bearing:
        return False
    return c.price <= 1.03  # >3% above $1 is a yield token DefiLlama hasn't flagged


def _share_label(c: Stablecoin) -> str | None:
    """The move as a share of the coin's own supply a week ago.

    A coin with no supply last week is not a 0% move, it is a launch, so it says
    so. More decimals on small moves: USDT's +$170M is +0.1%, not +0%.
    """
    if not c.prev_week:
        return "new" if c.change_7d and c.change_7d > 0 else None
    pct = c.change_7d / c.prev_week
    return fmt.pct(pct, digits=1 if abs(pct) < 0.1 else 0)


def _resolution_date(market: Market) -> date | None:
    """When the market settles. Polymarket sends ISO 8601, sometimes with a Z suffix."""
    if not market.end_date:
        return None
    try:
        return datetime.fromisoformat(market.end_date.replace("Z", "+00:00")).date()
    except (AttributeError, ValueError):
        return None


def _clear_of(lo: float, hi: float, span: float) -> tuple[float, str]:
    """Where to put a label so it sits beside a mark spanning lo..hi, not on it.
    Picks whichever side has more room before the axis edge."""
    pad = span * 0.035
    if (span - hi) >= (lo + span):
        return hi + pad, "left"
    return lo - pad, "right"


def _caption(text: str, sample: bool) -> str:
    return ("[SAMPLE DATA, DO NOT POST] " + text) if sample else text


_NOTE_SPOTS = [  # (dx, dy) in points, ha, va: tried in order, best clear spot wins
    (0, 10, "center", "bottom"), (0, -10, "center", "top"),
    (10, 8, "left", "bottom"), (-10, 8, "right", "bottom"),
    (10, -8, "left", "top"), (-10, -8, "right", "top"),
    (0, 26, "center", "bottom"), (0, -26, "center", "top"),
    (16, 0, "left", "center"), (-16, 0, "right", "center"),
]


def _line_pixels(ax, xs, ys, per_segment: int = 12) -> np.ndarray:
    """The line's path in display pixels, densified so steep jumps count too."""
    xn = np.asarray(mdates.date2num(xs) if not isinstance(xs[0], (int, float)) else xs, float)
    pts = ax.transData.transform(np.column_stack([xn, np.asarray(ys, float)]))
    t = np.linspace(0, 1, per_segment, endpoint=False)
    a, b = pts[:-1], pts[1:]
    dense = (a[:, None, :] + (b - a)[:, None, :] * t[None, :, None]).reshape(-1, 2)
    return np.vstack([dense, pts[-1:]])


def _note(ax, x, y, text, *, avoid=None) -> None:
    """Ringed marker at (x, y) plus a label placed where it clears the plotted line
    and stays inside the plot."""
    ax.scatter([x], [y], s=30, facecolor=BG, edgecolors=TEXT_2, linewidths=1.2, zorder=6)
    renderer = ax.figure.canvas.get_renderer()
    frame = ax.get_window_extent(renderer)
    line = _line_pixels(ax, *avoid) if avoid else np.empty((0, 2))
    best = None
    for dx, dy, ha, va in _NOTE_SPOTS:
        label = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", ha=ha,
                            va=va, fontsize=8.2, color=TEXT_2, zorder=6)
        box = label.get_window_extent(renderer).padded(8)
        hits = int(((line[:, 0] >= box.x0) & (line[:, 0] <= box.x1) &
                    (line[:, 1] >= box.y0) & (line[:, 1] <= box.y1)).sum())
        outside = not (frame.x0 <= box.x0 and box.x1 <= frame.x1 and
                       frame.y0 <= box.y0 and box.y1 <= frame.y1)
        score = hits + (10_000 if outside else 0)
        if best is None or score < best[0]:
            if best:
                best[1].remove()
            best = (score, label)
        else:
            label.remove()
        if score == 0:
            break


# 1. Total stablecoin supply -------------------------------------------------

def supply_chart(points: list[SupplyPoint], out: Path, *, days: int = 180,
                 sample: bool = False) -> Post:
    if len(points) < 31:
        raise ValueError("Need at least 31 days of supply history")
    recent = [p for p in points if p.day > points[-1].day - timedelta(days=days)]
    last = points[-1]
    month_ago = next((p for p in reversed(points) if p.day <= last.day - timedelta(days=30)),
                     points[0])
    change = last.total_usd - month_ago.total_usd
    change_pct = change / month_ago.total_usd
    all_time_high = max(points, key=lambda p: p.total_usd)
    is_record = last.total_usd >= all_time_high.total_usd * 0.9995

    verb = "grew" if change >= 0 else "shrank"
    if is_record:
        headline = f"Stablecoin supply hit a new all-time high, up {fmt.usd(abs(change))} in 30 days"
    else:
        headline = f"Stablecoin supply {verb} {fmt.usd(abs(change))} in the last 30 days"

    frame = new_frame(
        "Stablecoin supply", headline,
        f"Total circulating supply, all stablecoins, last {days} days",
        "Data: DefiLlama", _date_label(last.day),
        hero=fmt.usd(last.total_usd), hero_sub=f"{fmt.pct(change_pct)} vs 30 days ago",
        sample=sample,
    )
    ax = frame.ax
    xs = [p.day for p in recent]
    ys = [p.total_usd for p in recent]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.14 or lo * 0.01
    ax.set_ylim(lo - pad, hi + pad * 2.6)
    ax.set_xlim(xs[0], xs[-1] + timedelta(days=3))
    # The hero reads "vs 30 days ago"; shade that window so it can be seen, not just read.
    ax.axvspan(month_ago.day, xs[-1], color=BAND, zorder=0, linewidth=0)
    ax.annotate("last 30 days", (month_ago.day, 1), xycoords=("data", "axes fraction"),
                xytext=(4, -9), textcoords="offset points", ha="left", va="top",
                fontsize=7.8, color=TEXT_3)
    ax.fill_between(xs, ys, lo - pad, color=BRAND, alpha=0.08, linewidth=0)
    ax.plot(xs, ys, color=BRAND, linewidth=LINE_W, solid_capstyle="round", solid_joinstyle="round")
    end_dot(ax, xs[-1], ys[-1], BRAND)

    if is_record:
        ax.annotate("New high", (xs[-1], ys[-1]), xytext=(0, 10), textcoords="offset points",
                    ha="right", va="bottom", fontsize=8.2, color=TEXT_2)
    else:
        peak = max(recent, key=lambda p: p.total_usd)
        if peak.day != last.day:
            _note(ax, peak.day, peak.total_usd,
                  f"Peak {fmt.usd(peak.total_usd)} · {_short_date(peak.day)}",
                  avoid=(xs, ys))

    ax.yaxis.tick_right()
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt.usd(v, digits=0)))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    image = save(frame.fig, out / "supply.png")
    caption = (f"{headline}.\n\nTotal supply: {fmt.usd(last.total_usd)} "
               f"({fmt.pct(change_pct)} vs 30 days ago).\n\nData: DefiLlama")
    alt = (f"Line chart of total stablecoin supply over the last {days} days, ending at "
           f"{fmt.usd(last.total_usd)} on {_date_label(last.day)}, "
           f"{fmt.usd(change, sign=True)} over 30 days.")
    return Post("supply", image, headline, _caption(caption, sample), alt)


# 2. Biggest 7-day supply moves -------------------------------------------------

def movers_chart(coins: list[Stablecoin], out: Path, today: date, *, top: int = 8,
                 min_size: float = 50e6, sample: bool = False) -> Post:
    usd = [c for c in coins if c.peg_type == "peggedUSD" and c.change_7d is not None
           and c.circulating >= min_size]
    picked = sorted(usd, key=lambda c: abs(c.change_7d), reverse=True)[:top]
    if not picked:
        raise ValueError("No stablecoins with 7-day history")
    leader = picked[0]
    picked.sort(key=lambda c: c.change_7d)  # drawn bottom-up: biggest mint on top
    net = sum(c.change_7d for c in usd)
    name = _labels(picked, coins)

    action = "minted" if leader.change_7d >= 0 else "redeemed"
    headline = f"{fmt.usd(abs(leader.change_7d))} of {name[id(leader)]} was {action} this week"
    labels = [name[id(c)] for c in picked]
    frame = new_frame(
        "Supply flows · 7 days", headline,
        "Change in circulating supply and share of each coin's float, last 7 days",
        "Data: DefiLlama", _date_label(today),
        hero=fmt.usd(net, sign=True), hero_sub="net, all USD stablecoins",
        sample=sample, plot_left=label_margin(labels), plot_right=0.945,
    )
    ax = frame.ax
    vals = [c.change_7d for c in picked]
    # Share of the coin's own supply: $683M on a small coin is a very different
    # week from $1.2B on USDC, and the bar length alone cannot say which.
    pcts = [_share_label(c) for c in picked]
    ys = list(range(len(picked)))
    span = max(abs(v) for v in vals)
    # Room for "+$683M  +14%" past the bar end, and no further.
    ax.set_xlim(min(vals) - span * LABEL_ROOM if min(vals) < 0 else -span * 0.04,
                max(vals) + span * LABEL_ROOM if max(vals) > 0 else span * 0.04)
    ax.set_ylim(-0.6, len(picked) - 0.4)
    rounded_hbars(ax, ys, vals, [UP if v >= 0 else DOWN for v in vals])
    ax.axvline(0, color=TEXT_3, linewidth=0.7, zorder=3)
    for y, v, pc in zip(ys, vals, pcts):
        side = 1 if v >= 0 else -1
        ha = "left" if v >= 0 else "right"
        amount = fmt.usd(v, sign=True)
        ax.annotate(amount, (v, y), xytext=(5 * side, 0), textcoords="offset points",
                    va="center", ha=ha, fontsize=8.6, color=TEXT)
        if pc:  # mono, so the first label's width is len * char width
            ax.annotate(pc, (v, y),
                        xytext=((11 + len(amount) * MONO_CHAR_PT) * side, 0),
                        textcoords="offset points", va="center", ha=ha,
                        fontsize=8.2, color=TEXT_3)

    ax.set_yticks(ys, labels, fontsize=9, color=TEXT_2)
    ax.grid(False)  # the direct labels already carry every value
    ax.set_xticks([])
    for side in ("bottom", "top", "left", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(handles=[Patch(color=UP, label="Minted"), Patch(color=DOWN, label="Redeemed")],
              loc="upper left", bbox_to_anchor=(0, -0.01), ncol=2, frameon=False,
              fontsize=8.4, labelcolor=TEXT_2, handlelength=0.9, handleheight=0.9,
              columnspacing=1.2, borderaxespad=0)

    image = save(frame.fig, out / "movers.png")
    ranked = sorted(picked, key=lambda c: abs(c.change_7d), reverse=True)[:3]
    parts = "\n".join(
        f"{name[id(c)]} {fmt.usd(c.change_7d, sign=True)}"
        + (f" ({_share_label(c)})" if _share_label(c) else "") for c in ranked)
    caption = (f"{headline}.\n\nBiggest supply moves, last 7 days:\n{parts}\n\n"
               f"Net across all USD stablecoins: {fmt.usd(net, sign=True)}.\n\nData: DefiLlama")
    alt = "Bar chart of 7-day stablecoin supply change: " + "; ".join(
        f"{name[id(c)]} {fmt.usd(c.change_7d, sign=True)}" for c in reversed(picked)) + "."
    return Post("movers", image, headline, _caption(caption, sample), alt)


# 3. Peg watch ------------------------------------------------------------------

def peg_chart(coins: list[Stablecoin], out: Path, today: date, *,
              ranges: dict[str, PegRange] | None = None, top: int = 10,
              sample: bool = False) -> Post:
    usd = [c for c in coins if is_dollar_pegged(c)][:top]
    if not usd:
        raise ValueError("No USD stablecoins with prices")
    ranges = ranges or {}
    devs = [(c.price - 1) * 10_000 for c in usd]
    rngs = [ranges.get(c.gecko_id) for c in usd]
    off = [(c, d) for c, d in zip(usd, devs) if abs(d) > PEG_BAND_BPS]
    strayed = [(c, r) for c, r in zip(usd, rngs)
               if r and abs(r.worst_bps) > PEG_BAND_BPS]
    n = len(usd)
    window = f"{PEG_WINDOW_DAYS}-day"
    have_ranges = any(rngs)

    if off:
        if len(off) == 1:
            c, d = off[0]
            headline = (f"{c.symbol} slipped to ${c.price:.4f}, off its $1 peg" if d < 0
                        else f"{c.symbol} is trading above its peg at ${c.price:.4f}")
        else:
            headline = f"{len(off)} of the {n} biggest stablecoins are off their $1 peg"
    elif strayed:
        # Calm today, but something broke the band earlier in the window.
        if len(strayed) == 1:
            c, r = strayed[0]
            headline = (f"{c.symbol} hit {fmt.bps(r.worst_bps)} off peg this month, "
                        f"back near $1 today")
        else:
            headline = (f"{len(strayed)} of the {n} biggest stablecoins broke "
                        f"their peg band this month, all back now")
    elif have_ranges:
        headline = f"All {n} of the biggest stablecoins held their $1 peg this month"
    else:
        headline = f"All {n} of the biggest stablecoins are holding their $1 peg"

    subtitle = (f"Price and {window} range vs $1, top {n} USD stablecoins, no yield tokens"
                if have_ranges else
                f"Price vs $1, {n} largest USD stablecoins (yield-bearing excluded), in bps")
    frame = new_frame(
        "Peg watch", headline, subtitle,
        "Data: DefiLlama", _date_label(today),
        hero=f"{n - len(off)}/{n}",
        hero_sub=f"within {PEG_BAND_BPS} bps of $1 today" if have_ranges
                 else f"within {PEG_BAND_BPS} bps of $1",
        sample=sample, plot_left=label_margin([c.symbol for c in usd]), plot_right=0.945,
    )
    ax = frame.ax
    ys = list(range(n))[::-1]  # largest at the top
    reach = [abs(d) for d in devs] + [abs(v) for r in rngs if r
                                      for v in (r.low_bps, r.high_bps)]
    # Scale to the data, not to a fixed +/-100: on a calm day that floor squashed
    # every dot into one unreadable column in the middle.
    span = max(MIN_PEG_SPAN_BPS, max(reach) * 1.3)
    show_band = span >= PEG_BAND_BPS
    if show_band:
        ax.axvspan(-PEG_BAND_BPS, PEG_BAND_BPS, color=BAND, zorder=0, linewidth=0)
    ax.axvline(0, color=TEXT_3, linewidth=0.7, zorder=1)
    for y, c, d, r in zip(ys, usd, devs, rngs):
        if r and r.width_bps > 0:
            ax.plot([r.low_bps, r.high_bps], [y, y], color=RANGE, linewidth=4.6,
                    solid_capstyle="round", zorder=2)
        is_off = abs(d) > PEG_BAND_BPS
        ax.scatter([d], [y], s=52 if is_off else 34, color=WARN if is_off else NEUTRAL,
                   zorder=3, edgecolors=BG, linewidths=1.4)
        if is_off:
            x, ha = _clear_of(min(d, r.low_bps if r else d),
                              max(d, r.high_bps if r else d), span)
            ax.text(x, y, f"{fmt.bps(d)} · off peg", va="center", ha=ha,
                    fontsize=8.6, color=TEXT)

    widest = max((p for p in zip(ys, usd, rngs) if p[2]),
                 key=lambda p: p[2].width_bps, default=None)
    if not off and widest:  # one annotation: the month's widest wobble
        y, c, r = widest
        x, ha = _clear_of(r.low_bps, r.high_bps, span)
        ax.text(x, y, f"{round(r.width_bps)} bps range in {PEG_WINDOW_DAYS} days",
                va="center", ha=ha, fontsize=8.4, color=TEXT_2)

    ax.set_xlim(-span, span)
    ax.set_ylim(-0.7, n - 0.3)
    ax.set_yticks(ys, [c.symbol for c in usd], fontsize=9, color=TEXT_2)
    ax.grid(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.spines["bottom"].set_visible(False)
    ax.xaxis.set_major_locator(MultipleLocator(50) if 30 < span <= 150
                               else MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10],
                                                symmetric=True))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}" if v else "$1.00"))
    if show_band:
        ax.text(PEG_BAND_BPS, n - 0.45, f" ±{PEG_BAND_BPS} bps", fontsize=8, color=TEXT_3,
                va="top", ha="left")

    image = save(frame.fig, out / "pegs.png")
    lines = []
    if off:
        lines.append("\n".join(f"{c.symbol}: ${c.price:.4f} ({fmt.bps(d)})" for c, d in off))
        lines.append(f"Everyone else in the top {n} is within 0.5% of $1.")
    else:
        worst_c, worst_d = max(zip(usd, devs), key=lambda t: abs(t[1]))
        lines.append(f"Widest gap today: {worst_c.symbol} at ${worst_c.price:.4f} "
                     f"({fmt.bps(worst_d)}).")
    if widest:
        _, c, r = widest
        lines.append(f"Widest {window} swing: {c.symbol}, {fmt.bps(r.low_bps)} to "
                     f"{fmt.bps(r.high_bps)}.")
    caption = f"{headline}.\n\n" + "\n\n".join(lines) + "\n\nData: DefiLlama"

    if have_ranges:
        def _say(c: Stablecoin, d: float, r: PegRange | None) -> str:
            if r is None:
                return f"{c.symbol} {fmt.bps(d)}"
            if round(r.low_bps) == round(r.high_bps):
                return f"{c.symbol} {fmt.bps(d)}, flat for all {PEG_WINDOW_DAYS} days"
            return (f"{c.symbol} {fmt.bps(d)}, {window} {fmt.bps(r.low_bps)} "
                    f"to {fmt.bps(r.high_bps)}")
        alt = (f"Dot plot of each stablecoin's price vs $1 today with its {window} range: "
               + "; ".join(_say(c, d, r) for c, d, r in zip(usd, devs, rngs)) + ".")
    else:
        alt = "Dot plot of each stablecoin's price deviation from $1: " + "; ".join(
            f"{c.symbol} {fmt.bps(d)}" for c, d in zip(usd, devs)) + "."
    return Post("pegs", image, headline, _caption(caption, sample), alt)


# 4. Prediction market odds --------------------------------------------------------

def _biggest_daily_swing(history: list[PricePoint]) -> tuple[PricePoint, float] | None:
    best, j = None, 0
    for p in history:
        while history[j].at < p.at - timedelta(hours=24):
            j += 1
        if j == 0 and history[0].at > p.at - timedelta(hours=20):
            continue  # not a full day of history behind this point yet
        move = (p.price - history[j].price) * 100
        if best is None or abs(move) > abs(best[1]):
            best = (p, move)
    return best


def odds_chart(market: Market, history: list[PricePoint], out: Path, *,
               sample: bool = False) -> Post:
    if len(history) < 2:
        raise ValueError(f"No price history for '{market.slug}'")
    now = history[-1].price * 100
    week_ago = next((p for p in reversed(history) if p.at <= history[-1].at - timedelta(days=7)),
                    history[0])
    move = now - week_ago.price * 100
    trend = "flat this week" if abs(move) < 1 else f"{move:+.0f} pts this week"
    span_move = now - history[0].price * 100
    peak = max(history, key=lambda p: p.price)

    today = history[-1].at.date()
    span_days = max(1, round((history[-1].at - history[0].at).total_seconds() / 86400))
    resolves = _resolution_date(market)
    resolves_note = f" · resolves {_short_date(resolves)}" if resolves else ""
    frame = new_frame(
        "Polymarket odds", market.question,
        f"Chance of Yes, last {span_days} days · "
        f"{fmt.usd(market.volume_24h)} traded in 24h{resolves_note}",
        "Data: Polymarket", _date_label(today),
        hero=f"{now:.0f}%",
        hero_sub=(f"{span_move:+.0f} pts in {span_days} days"
                  if abs(move) < 1 and abs(span_move) >= 5 else trend),
        sample=sample,
    )
    ax = frame.ax
    xs = [p.at for p in history]
    ys = [p.price * 100 for p in history]
    ax.set_ylim(0, 100)
    ax.set_xlim(xs[0], xs[-1] + (xs[-1] - xs[0]) * 0.03)
    ax.plot(xs, ys, color=BRAND, linewidth=LINE_W, solid_joinstyle="round", solid_capstyle="round")
    end_dot(ax, xs[-1], ys[-1], BRAND)

    swing = _biggest_daily_swing(history)
    if swing and abs(swing[1]) >= 5 and swing[0].at != xs[-1]:
        p, mv = swing
        _note(ax, p.at, p.price * 100, f"{mv:+.0f} pts in a day · {_short_date(p.at.date())}",
              avoid=(xs, ys))

    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MultipleLocator(25))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    step = 7 if span_days <= 45 else 14
    ticks = [xs[-1] - timedelta(days=d) for d in range(0, span_days + 1, step)][::-1]
    ax.set_xticks([t for t in ticks if t >= xs[0]])
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    image = save(frame.fig, out / "odds.png")
    context = ""
    if abs(span_move) >= 5:
        context = f", {'up' if span_move > 0 else 'down'} {abs(span_move):.0f} pts over {span_days} days"
        if peak.price * 100 - now >= 10:
            context += f" (it peaked at {peak.price * 100:.0f}% on {_short_date(peak.at.date())})"
    settles = f" Market resolves {_short_date(resolves)}." if resolves else ""
    caption = (f"Polymarket: {market.question}\n\nYes is at {now:.0f}%, {trend}{context}. "
               f"{fmt.usd(market.volume_24h)} traded in the last 24 hours.{settles}"
               f"\n\nData: Polymarket")
    alt = (f"Line chart of Polymarket odds for '{market.question}' over {span_days} days, "
           f"now {now:.0f}%, {trend}.")
    return Post("odds", image, market.question, _caption(caption, sample), alt)
