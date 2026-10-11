"""The daily Tidewatch charts. Each builder saves a PNG and returns a Post.

Every chart leads with a headline that states the finding (computed from the
data), backs it with one hero number, and annotates at most one point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import numpy as np
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, MaxNLocator, MultipleLocator

from . import fmt
from .defillama import Stablecoin, SupplyPoint
from .polymarket import Market, PricePoint
from .style import (BAND, BG, BRAND, DOWN, GRID, LINE_W, NEUTRAL, TEXT, TEXT_2, TEXT_3, UP,
                    WARN, end_dot, label_margin, new_frame, rounded_hbars, save)

PEG_BAND_BPS = 50


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
        "Biggest changes in circulating supply, last 7 days",
        "Data: DefiLlama", _date_label(today),
        hero=fmt.usd(net, sign=True), hero_sub="net, all USD stablecoins",
        sample=sample, plot_left=label_margin(labels), plot_right=0.945,
    )
    ax = frame.ax
    vals = [c.change_7d for c in picked]
    ys = list(range(len(picked)))
    span = max(abs(v) for v in vals)
    ax.set_xlim(-span * 1.38 if min(vals) < 0 else -span * 0.04,
                span * 1.38 if max(vals) > 0 else span * 0.04)
    ax.set_ylim(-0.6, len(picked) - 0.4)
    rounded_hbars(ax, ys, vals, [UP if v >= 0 else DOWN for v in vals])
    ax.axvline(0, color=TEXT_3, linewidth=0.7, zorder=3)
    for y, v in zip(ys, vals):
        ax.text(v + (span * 0.025 if v >= 0 else -span * 0.025), y, fmt.usd(v, sign=True),
                va="center", ha="left" if v >= 0 else "right", fontsize=8.6, color=TEXT)

    ax.set_yticks(ys, labels, fontsize=9, color=TEXT_2)
    ax.grid(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.spines["bottom"].set_visible(False)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt.usd(v, digits=0, sign=v > 0)))
    legend_loc = "lower right" if vals[0] < 0 else "upper left"
    ax.legend(handles=[Patch(color=UP, label="Minted"), Patch(color=DOWN, label="Redeemed")],
              loc=legend_loc, ncol=2, frameon=False, fontsize=8.4, labelcolor=TEXT_2,
              handlelength=0.9, handleheight=0.9, columnspacing=1.2, borderaxespad=0.3)

    image = save(frame.fig, out / "movers.png")
    ranked = sorted(picked, key=lambda c: abs(c.change_7d), reverse=True)[:3]
    parts = "\n".join(f"{name[id(c)]} {fmt.usd(c.change_7d, sign=True)}" for c in ranked)
    caption = (f"{headline}.\n\nBiggest supply moves, last 7 days:\n{parts}\n\n"
               f"Net across all USD stablecoins: {fmt.usd(net, sign=True)}.\n\nData: DefiLlama")
    alt = "Bar chart of 7-day stablecoin supply change: " + "; ".join(
        f"{name[id(c)]} {fmt.usd(c.change_7d, sign=True)}" for c in reversed(picked)) + "."
    return Post("movers", image, headline, _caption(caption, sample), alt)


# 3. Peg watch ------------------------------------------------------------------

def peg_chart(coins: list[Stablecoin], out: Path, today: date, *, top: int = 10,
              sample: bool = False) -> Post:
    usd = [c for c in coins if is_dollar_pegged(c)][:top]
    if not usd:
        raise ValueError("No USD stablecoins with prices")
    devs = [(c.price - 1) * 10_000 for c in usd]
    off = [(c, d) for c, d in zip(usd, devs) if abs(d) > PEG_BAND_BPS]
    n = len(usd)

    if not off:
        headline = f"All {n} of the biggest stablecoins are holding their $1 peg"
    elif len(off) == 1:
        c, d = off[0]
        headline = (f"{c.symbol} slipped to ${c.price:.4f}, off its $1 peg" if d < 0
                    else f"{c.symbol} is trading above its peg at ${c.price:.4f}")
    else:
        headline = f"{len(off)} of the {n} biggest stablecoins are off their $1 peg"

    frame = new_frame(
        "Peg watch", headline,
        f"Price vs $1, {n} largest USD stablecoins (yield-bearing excluded), in bps",
        "Data: DefiLlama", _date_label(today),
        hero=f"{n - len(off)}/{n}", hero_sub=f"within {PEG_BAND_BPS} bps of $1",
        sample=sample, plot_left=label_margin([c.symbol for c in usd]), plot_right=0.945,
    )
    ax = frame.ax
    ys = list(range(n))[::-1]  # largest at the top
    span = max(100.0, max(abs(d) for d in devs) * 1.3)
    ax.axvspan(-PEG_BAND_BPS, PEG_BAND_BPS, color=BAND, zorder=0, linewidth=0)
    ax.axvline(0, color=TEXT_3, linewidth=0.7, zorder=1)
    for y, c, d in zip(ys, usd, devs):
        is_off = abs(d) > PEG_BAND_BPS
        ax.scatter([d], [y], s=52 if is_off else 34, color=WARN if is_off else NEUTRAL,
                   zorder=3, edgecolors=BG, linewidths=1.4)
        if is_off:  # label points toward the center, away from the axis edges
            ax.text(d + (-span * 0.045 if d >= 0 else span * 0.045), y, f"{fmt.bps(d)} · off peg",
                    va="center", ha="right" if d >= 0 else "left", fontsize=8.6, color=TEXT)
    ax.set_xlim(-span, span)
    ax.set_ylim(-0.7, n - 0.3)
    ax.set_yticks(ys, [c.symbol for c in usd], fontsize=9, color=TEXT_2)
    ax.grid(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.spines["bottom"].set_visible(False)
    ax.xaxis.set_major_locator(MultipleLocator(50) if span <= 150
                               else MaxNLocator(nbins=6, steps=[1, 2, 2.5, 5, 10], symmetric=True))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}" if v else "$1.00"))
    ax.text(PEG_BAND_BPS, n - 0.45, f" ±{PEG_BAND_BPS} bps", fontsize=8, color=TEXT_3,
            va="top", ha="left")

    image = save(frame.fig, out / "pegs.png")
    if off:
        detail = "\n".join(f"{c.symbol}: ${c.price:.4f} ({fmt.bps(d)})" for c, d in off)
        caption = f"{headline}.\n\n{detail}\n\nEveryone else in the top {n} is within 0.5% of $1.\n\nData: DefiLlama"
    else:
        worst_c, worst_d = max(zip(usd, devs), key=lambda t: abs(t[1]))
        caption = (f"{headline}.\n\nWidest gap today: {worst_c.symbol} at ${worst_c.price:.4f} "
                   f"({fmt.bps(worst_d)}).\n\nData: DefiLlama")
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
    frame = new_frame(
        "Polymarket odds", market.question,
        f"Chance of Yes, last {span_days} days · {fmt.usd(market.volume_24h)} traded in 24h",
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
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=7))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))

    image = save(frame.fig, out / "odds.png")
    context = ""
    if abs(span_move) >= 5:
        context = f", {'up' if span_move > 0 else 'down'} {abs(span_move):.0f} pts over {span_days} days"
        if peak.price * 100 - now >= 10:
            context += f" (it peaked at {peak.price * 100:.0f}% on {_short_date(peak.at.date())})"
    caption = (f"Polymarket: {market.question}\n\nYes is at {now:.0f}%, {trend}{context}. "
               f"{fmt.usd(market.volume_24h)} traded in the last 24 hours.\n\nData: Polymarket")
    alt = (f"Line chart of Polymarket odds for '{market.question}' over {span_days} days, "
           f"now {now:.0f}%, {trend}.")
    return Post("odds", image, market.question, _caption(caption, sample), alt)
