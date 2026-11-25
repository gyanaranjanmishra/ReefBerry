"""Draw the ReefBerry logo: an Acropora coral traced from a reef photo.

Writes reefberry/static/img/logo.svg, plus two square icon SVGs in tools/out/.
The PNG app icons in static/img were made from those: render icon-full.svg and
icon-maskable.svg at 512x512 (e.g. a headless browser screenshot), then shrink the
512 render to 192 (icon-192.png) and 180 (apple-touch-icon.png).

    python tools/make_logo.py
"""

from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "reefberry" / "static" / "img"
BG = "#081226"

# Colour along a branch: yellow-green base, peach, pink, lavender, blue-white tip.
RAMP = [("#B9C44C", 0), ("#E9B25E", .18), ("#F3A07E", .4), ("#F08DA8", .62),
        ("#E58BD0", .7), ("#B6A6F6", .8), ("#8FD8FF", .9), ("#D8F6FF", 1)]

# Main branches as quadratic curves (start, control, end) and width.
MAIN = {
    "B": ([(20, 186), (36, 112), (66, 34)], 24),
    "A": ([(20, 186), (70, 138), (160, 116)], 26),
    "C": ([(22, 190), (96, 194), (182, 166)], 22),
}
# Side branchlets: parent, where on the parent, control point, tip, width.
KIDS = [("B", .5, (92, 86), (114, 38), 17), ("A", .4, (82, 108), (104, 64), 18),
        ("A", .62, (122, 98), (150, 58), 17), ("A", .85, (160, 104), (184, 86), 15),
        ("C", .55, (128, 166), (170, 140), 16), ("B", .3, (14, 104), (16, 70), 15)]


def q(p, t):
    a = 1 - t
    return (a * a * p[0][0] + 2 * a * t * p[1][0] + t * t * p[2][0],
            a * a * p[0][1] + 2 * a * t * p[1][1] + t * t * p[2][1])


def dq(p, t):
    a = 1 - t
    return (2 * a * (p[1][0] - p[0][0]) + 2 * t * (p[2][0] - p[1][0]),
            2 * a * (p[1][1] - p[0][1]) + 2 * t * (p[2][1] - p[1][1]))


def mix(c1, c2, f):
    f = max(0.0, min(1.0, f))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def ramp(t):
    for (c0, s0), (c1, s1) in zip(RAMP, RAMP[1:]):
        if t <= s1:
            return mix(c0, c1, (t - s0) / (s1 - s0))
    return RAMP[-1][0]


def f1(v):
    return f"{v:.1f}"


def branches():
    kids = []
    for par, at, c, e, w in KIDS:
        start = q(MAIN[par][0], at)
        kids.append(([start, c, e], w, .25 + at * .35))
    m = {k: (p, w, 0.0) for k, (p, w) in MAIN.items()}
    # Back to front, so nearer branches overlap farther ones.
    return [m["B"], kids[5], kids[0], m["A"], kids[1], kids[2], kids[3], m["C"], kids[4]]


def coral() -> str:
    s = []
    for p, w0, t0 in branches():
        def span(t):
            return t0 + (1 - t0) * t

        def wd(t):
            return w0 * (1 - .38 * t)

        for i in range(22):
            a, c, t1 = q(p, i / 22), q(p, (i + 1) / 22), (i + 1) / 22
            s.append(f'<line x1="{f1(a[0])}" y1="{f1(a[1])}" x2="{f1(c[0])}" y2="{f1(c[1])}" '
                     f'stroke-width="{f1(wd(t1))}" stroke-linecap="round" '
                     f'stroke="{mix(ramp(span(t1)), "#000000", .12)}"/>')
        length, p0 = 0.0, q(p, 0)
        for i in range(1, 41):
            p1 = q(p, i / 40)
            length += math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            p0 = p1
        n = int(length / 5.2)
        for i in range(1, n):
            t = i / n
            if t > .93:
                break
            c, g = q(p, t), dq(p, t)
            L = math.hypot(*g)
            tx, ty = g[0] / L, g[1] / L
            w, col = wd(t), ramp(span(t))
            mouth = "#E0662E" if span(t) < .5 else "#D8407E"
            # Corallites along both edges, alternating sides.
            for k, sd in enumerate((1, -1)):
                if (i + k) % 2:
                    continue
                nx, ny = -ty * sd, tx * sd
                ang = math.degrees(math.atan2(ny * .8 + ty * .6, nx * .8 + tx * .6))
                x, y, cl = c[0] + nx * w * .36, c[1] + ny * w * .36, 7.5 - 2 * t
                s.append(f'<g transform="translate({f1(x)} {f1(y)}) rotate({ang:.0f})">'
                         f'<rect x="-2" y="-3.4" width="{f1(cl + 2)}" height="6.8" rx="3.4" '
                         f'fill="{mix(col, "#ffffff", .2)}" stroke="{mix(col, "#000000", .3)}" stroke-width=".7"/>'
                         f'<ellipse cx="{f1(cl - 1)}" cy="0" rx="1.3" ry="2" fill="{mouth}"/></g>')
            # Corallites facing the viewer, every second step.
            if i % 2 == 0:
                off = (1 if (i // 2) % 2 else -1) * w * .12
                x, y = c[0] - ty * off, c[1] + tx * off
                ang = math.degrees(math.atan2(ty, tx))
                s.append(f'<g transform="translate({f1(x)} {f1(y)}) rotate({ang:.0f})">'
                         f'<ellipse rx="3.4" ry="2.9" fill="{mix(col, "#ffffff", .28)}" '
                         f'stroke="{mix(col, "#000000", .3)}" stroke-width=".6"/>'
                         f'<circle cx="1" r="1.1" fill="{mouth}"/></g>')
        # Glowing axial corallite at the tip.
        e, g = q(p, 1), dq(p, 1)
        ang, tw = math.degrees(math.atan2(g[1], g[0])), wd(1) * .95
        s.append(f'<g transform="translate({f1(e[0])} {f1(e[1])}) rotate({ang:.0f})">'
                 f'<rect x="{f1(-tw * .6)}" y="{f1(-tw / 2)}" width="{f1(tw * 1.25)}" height="{f1(tw)}" '
                 f'rx="{f1(tw / 2)}" fill="#E6F9FF" stroke="#3FB8EE" stroke-width="1.8"/>'
                 f'<ellipse cx="{f1(tw * .28)}" rx="2" ry="2.6" fill="#3FB8EE"/></g>')
    return "".join(s)


def svg(inner: str, *, rounded: bool, scale: float = .92) -> str:
    # Keep the coral centred whatever the scale (the drawing fills about 0..200).
    off = (200 - 200 * scale) / 2
    rx = ' rx="44"' if rounded else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
            f'<rect width="200" height="200"{rx} fill="{BG}"/>'
            f'<g transform="translate({f1(off)} {f1(off - 2)}) scale({scale})">{inner}</g></svg>\n')


def main() -> None:
    inner = coral()
    files = {
        "logo.svg": svg(inner, rounded=True),                 # favicon and top bar
        "icon-full.svg": svg(inner, rounded=False),          # square app icons
        "icon-maskable.svg": svg(inner, rounded=False, scale=.72),  # Android crops to a circle
    }
    extra = Path(__file__).resolve().parent / "out"
    extra.mkdir(exist_ok=True)
    for name, text in files.items():
        path = (OUT if name == "logo.svg" else extra) / name
        path.write_text(text, encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
