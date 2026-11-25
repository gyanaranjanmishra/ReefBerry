"""Small line charts drawn as SVG on the Pi, so they work without internet access.

Colours come from CSS classes (chart-*), so the charts follow light and dark mode.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from html import escape

from markupsafe import Markup

W, H = 640, 220
LEFT, RIGHT, TOP, BOTTOM = 52, 16, 14, 30


def _nice_step(span: float, target: int = 4) -> float:
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _fmt(v: float, decimals: int) -> str:
    return f"{v:.{decimals}f}"


def line_chart(
    points: list[tuple[datetime, float]],
    *,
    low: float | None,
    high: float | None,
    unit: str,
    decimals: int,
    label: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Markup:
    """A line of readings over time with the green band shaded behind it."""
    if not points:
        return Markup("")
    times = [t for t, _ in points]
    values = [v for _, v in points]
    end = end or max(times)
    start = start or min(times)
    if start >= end:
        start, end = start - timedelta(days=1), end + timedelta(days=1)

    # Y range: the readings and the band, with a little room around them.
    lo_vals = values + [x for x in (low, high) if x is not None]
    y0, y1 = min(lo_vals), max(lo_vals)
    if y0 == y1:
        pad = abs(y0) * .1 or 1
        y0, y1 = y0 - pad, y1 + pad
    pad = (y1 - y0) * .12
    y0, y1 = y0 - pad, y1 + pad
    if min(lo_vals) >= 0:
        y0 = max(y0, 0)
    step = _nice_step(y1 - y0)
    y0, y1 = math.floor(y0 / step) * step, math.ceil(y1 / step) * step
    tick_dec = max(decimals, max(0, -math.floor(math.log10(step))) if step < 1 else 0)

    pw, ph = W - LEFT - RIGHT, H - TOP - BOTTOM
    span_s = (end - start).total_seconds()

    def x(t: datetime) -> float:
        return LEFT + pw * (t - start).total_seconds() / span_s

    def y(v: float) -> float:
        return TOP + ph * (1 - (v - y0) / (y1 - y0))

    parts = []
    # Green band.
    if low is not None or high is not None:
        b_top = y(min(high, y1)) if high is not None else TOP
        b_bot = y(max(low, y0)) if low is not None else TOP + ph
        if b_bot > b_top:
            parts.append(f'<rect class="chart-band" x="{LEFT}" y="{b_top:.1f}" width="{pw}" height="{b_bot - b_top:.1f}"/>')
    # Grid and y labels.
    n = int(round((y1 - y0) / step))
    for i in range(n + 1):
        v = y0 + i * step
        yy = y(v)
        parts.append(f'<line class="chart-grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{yy:.1f}" y2="{yy:.1f}"/>')
        parts.append(f'<text class="chart-axis" x="{LEFT - 8}" y="{yy + 4:.1f}" text-anchor="end">{_fmt(v, tick_dec)}</text>')
    # X labels: start, middle, end.
    for i, t in enumerate((start, start + (end - start) / 2, end)):
        anchor = ("start", "middle", "end")[i]
        parts.append(f'<text class="chart-axis" x="{x(t):.1f}" y="{H - 8}" text-anchor="{anchor}">{t.strftime("%d %b")}</text>')
    # Line and dots.
    if len(points) > 1:
        d = " ".join(f"{'M' if i == 0 else 'L'}{x(t):.1f} {y(v):.1f}" for i, (t, v) in enumerate(points))
        parts.append(f'<path class="chart-line" d="{d}"/>')
    for t, v in points:
        out = (low is not None and v < low) or (high is not None and v > high)
        cls = "chart-dot out" if out else "chart-dot"
        tip = escape(f"{_fmt(v, decimals)} {unit} on {t.strftime('%d %b %Y, %H:%M')}")
        parts.append(f'<circle class="{cls}" cx="{x(t):.1f}" cy="{y(v):.1f}" r="4.5"><title>{tip}</title></circle>')

    last_t, last_v = points[-1]
    summary = escape(
        f"{label}: {len(points)} readings from {start:%d %b %Y} to {end:%d %b %Y}; "
        f"latest {_fmt(last_v, decimals)} {unit} on {last_t:%d %b %Y}."
    )
    return Markup(
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{summary}">{"".join(parts)}</svg>'
    )
