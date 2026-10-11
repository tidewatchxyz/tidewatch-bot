"""Number formatting for chart labels and captions."""

from __future__ import annotations

MINUS = "-"  # plain hyphen so captions paste cleanly everywhere


def usd(value: float, digits: int = 1, sign: bool = False) -> str:
    neg = value < 0
    v = abs(value)
    if v >= 1e12:
        s = f"${v / 1e12:.{digits + 1}f}T"
    elif v >= 1e9:
        s = f"${v / 1e9:.{digits}f}B"
    elif v >= 1e6:
        s = f"${v / 1e6:.0f}M"
    elif v >= 1e3:
        s = f"${v / 1e3:.0f}K"
    else:
        s = f"${v:.0f}"
    if neg:
        return MINUS + s
    return ("+" + s) if sign else s


def pct(value: float, digits: int = 1, sign: bool = True) -> str:
    s = f"{abs(value) * 100:.{digits}f}%"
    if value < 0:
        return MINUS + s
    return ("+" + s) if sign else s


def bps(value: float) -> str:
    n = round(value)
    return f"{MINUS}{abs(n)} bps" if n < 0 else f"+{n} bps"
