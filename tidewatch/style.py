"""Tidewatch visual style: colors, fonts and the shared chart frame.

Every chart is 1600x900 px (16:9, shown uncropped in the X feed), dark
background, one brand accent. The marks palette was checked with a CVD
validator against the dark surface: mint #1FA874 vs orange #D95926 passes
the lightness band, CVD separation, normal-vision and contrast checks.

Frame anatomy, top to bottom:
  KICKER (what the chart is)                         HERO NUMBER
  Headline that states the finding,                  hero sub-line
  auto-sized to fit in at most two lines
  subtitle (exactly what is plotted)
  [ plot ]
  TIDEWATCH ------------------------------ source · date
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from matplotlib import font_manager  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402

FONT_DIR = Path(__file__).parent / "fonts"

# Surfaces and ink
BG = "#0D1117"
BAND = "#131B26"
GRID = "#1C2430"
AXIS = "#2A3340"
TEXT = "#F4F6F8"
TEXT_2 = "#B7C2CF"
TEXT_3 = "#8693A3"

# Marks
BRAND = "#3DDC97"  # single-series lines (brand mint, same as pfp/banner)
UP = "#1FA874"  # supply minted / positive change
DOWN = "#D95926"  # supply redeemed / negative change
NEUTRAL = "#6B7788"  # unremarkable dots
WARN = "#C98500"  # status: off peg (always paired with a text label)

WIDTH_IN, HEIGHT_IN, DPI = 6.4, 3.6, 250  # -> 1600 x 900 px
LEFT, RIGHT = 0.055, 0.945
LINE_W = 1.7  # pt
DOT_S = 42  # pt^2


@dataclass(frozen=True)
class Fonts:
    display: str
    mono: str


_fonts: Fonts | None = None


def fonts() -> Fonts:
    """Register the bundled fonts once and return their family names."""
    global _fonts
    if _fonts is None:
        display, mono = "DejaVu Sans", "DejaVu Sans Mono"
        for path in sorted(FONT_DIR.glob("*.ttf")):
            font_manager.fontManager.addfont(str(path))
            name = font_manager.FontProperties(fname=str(path)).get_name()
            if "Archivo" in name:
                display = name
            elif "Plex Mono" in name:
                mono = name
        _fonts = Fonts(display=display, mono=mono)
        plt.rcParams.update(
            {
                "font.family": mono,
                "text.color": TEXT_2,
                "axes.edgecolor": AXIS,
                "axes.labelcolor": TEXT_3,
                "xtick.color": TEXT_3,
                "ytick.color": TEXT_3,
                "xtick.labelsize": 9,
                "ytick.labelsize": 9,
                "axes.facecolor": BG,
                "figure.facecolor": BG,
                "savefig.facecolor": BG,
            }
        )
    return _fonts


@dataclass
class Frame:
    fig: Figure
    ax: matplotlib.axes.Axes


def _pt_to_frac(pt: float) -> float:
    return pt / 72 / HEIGHT_IN


def _text_width(fig: Figure, text: str, **kw) -> float:
    """Rendered width of text as a fraction of figure width."""
    t = fig.text(0, 0, text, **kw)
    w = t.get_window_extent(fig.canvas.get_renderer()).width / fig.bbox.width
    t.remove()
    return w


def _wrap_to_width(fig: Figure, text: str, width: float, **kw) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and _text_width(fig, trial, **kw) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit_headline(fig: Figure, text: str, width: float, family: str,
                 sizes=(19, 17, 15, 13.5), max_lines: int = 2) -> tuple[list[str], float]:
    """Largest font size at which text wraps into max_lines within width."""
    for size in sizes:
        lines = _wrap_to_width(fig, text, width, fontfamily=family, fontsize=size)
        if len(lines) <= max_lines:
            return lines, size
    lines = _wrap_to_width(fig, text, width, fontfamily=family, fontsize=sizes[-1])
    kept = lines[:max_lines]
    kept[-1] = kept[-1].rstrip(".,;:") + "..."
    return kept, sizes[-1]


def new_frame(
    kicker: str,
    headline: str,
    subtitle: str,
    source: str,
    date_label: str,
    *,
    hero: str | None = None,
    hero_sub: str | None = None,
    sample: bool = False,
    plot_left: float | None = None,
    plot_right: float | None = None,
) -> Frame:
    f = fonts()
    fig = plt.figure(figsize=(WIDTH_IN, HEIGHT_IN), dpi=DPI)

    top = 0.935
    fig.text(LEFT, top, kicker.upper(), fontsize=7.5, fontweight="medium", color=BRAND,
             va="top", ha="left")

    head_right = RIGHT
    if hero:
        hero_size = 25
        fig.text(RIGHT, top + 0.005, hero, fontfamily=f.mono, fontweight="semibold",
                 fontsize=hero_size, color=TEXT, ha="right", va="top")
        hero_w = _text_width(fig, hero, fontfamily=f.mono, fontweight="semibold",
                             fontsize=hero_size)
        y_sub = top - _pt_to_frac(hero_size * 1.2)
        if hero_sub:
            fig.text(RIGHT, y_sub, hero_sub, fontsize=8.6, color=TEXT_2, ha="right", va="top")
            hero_w = max(hero_w, _text_width(fig, hero_sub, fontsize=8.6))
        head_right = RIGHT - hero_w - 0.045

    lines, size = fit_headline(fig, headline, head_right - LEFT, f.display)
    y = top - _pt_to_frac(7.5 * 1.9)
    fig.text(LEFT, y, "\n".join(lines), fontfamily=f.display, fontsize=size, color=TEXT,
             va="top", ha="left", linespacing=1.12)
    y -= _pt_to_frac(size * 1.12 * len(lines) + 7)
    fig.text(LEFT, y, subtitle, fontsize=8.6, color=TEXT_2, va="top", ha="left")
    plot_top = y - _pt_to_frac(8.6 * 1.3 + 14)

    pl = LEFT if plot_left is None else plot_left
    pr = (RIGHT - 0.065) if plot_right is None else plot_right
    bottom = 0.155
    ax = fig.add_axes((pl, bottom, pr - pl, plot_top - bottom))
    style_axes(ax)

    # Footer: wordmark left, source right, hairline above.
    fig.add_artist(Line2D([LEFT, RIGHT], [0.085, 0.085], color=GRID, linewidth=0.6))
    fig.text(LEFT, 0.042, "TIDEWATCH", fontfamily=f.display, fontsize=8.5, color=BRAND,
             va="center", ha="left")
    fig.text(RIGHT, 0.042, f"{source}  ·  {date_label}", fontsize=7, color=TEXT_3,
             va="center", ha="right")

    if sample:
        fig.text(0.5, 0.5, "SAMPLE DATA · NOT REAL", fontfamily=f.display, fontsize=34,
                 color="#FF4D4D", alpha=0.25, rotation=12, ha="center", va="center", zorder=100)

    return Frame(fig=fig, ax=ax)


def label_margin(labels: list[str], fontsize: float = 9) -> float:
    """Left edge for a plot whose y tick labels are these strings (mono font)."""
    longest = max((len(t) for t in labels), default=4)
    char = fontsize * 0.6 / 72 / WIDTH_IN
    return LEFT + longest * char + 0.014


def style_axes(ax) -> None:
    ax.set_facecolor(BG)
    for side in ("top", "left", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.spines["bottom"].set_linewidth(0.6)
    ax.tick_params(length=0, pad=6)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def rounded_hbars(ax, ys, values, colors, height: float = 0.58, radius_px: float = 10) -> None:
    """Horizontal bars, square at the zero baseline and rounded at the data end.

    Call after the axis limits are final (the radius is converted from pixels).
    """
    fig = ax.figure
    bbox = ax.get_window_extent(fig.canvas.get_renderer())
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    x_per_px = (x1 - x0) / bbox.width
    y_per_px = abs(y1 - y0) / bbox.height
    for y, v, c in zip(ys, values, colors):
        if v == 0:
            continue
        s = 1 if v > 0 else -1
        rx = min(radius_px * x_per_px, abs(v))
        ry = min(radius_px * y_per_px, height / 2)
        y0, y1 = y - height / 2, y + height / 2
        # One closed outline: square at 0, quadratic-curve corners at the data end.
        verts = [(0, y0), (v - s * rx, y0), (v, y0), (v, y0 + ry), (v, y1 - ry), (v, y1),
                 (v - s * rx, y1), (0, y1), (0, y0)]
        codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO,
                 MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MPath(verts, codes), facecolor=c, edgecolor="none", zorder=2))


def end_dot(ax, x, y, color) -> None:
    ax.scatter([x], [y], s=DOT_S, color=color, zorder=5, edgecolors=BG, linewidths=1.6)


def save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    return path
