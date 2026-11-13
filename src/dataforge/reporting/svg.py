"""Dependency-free, deterministic SVG charts for reports.

Design rules (validated palette, both modes): thin bars (<= 16px) with a 4px rounded data
end and a square baseline, solid hairline gridlines, text in ink tokens (never the series
color), a legend only for two or more series, native ``<title>`` tooltips on every mark,
and light/dark variants through ``prefers-color-scheme``. Every plotted value is also
present in the report's tables, so a chart never gates information.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from html import escape
from typing import Final

_FONT: Final = 'system-ui, -apple-system, "Segoe UI", sans-serif'
_STYLE: Final = f"""
  .viz {{ --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781; --grid:#e1e0d9;
          --base:#c3c2b7; --s1:#2a78d6; --s2:#eb6834; font-family:{_FONT}; }}
  @media (prefers-color-scheme: dark) {{
    .viz {{ --surface:#1a1a19; --ink:#f0efec; --ink2:#c3c2b7; --muted:#898781; --grid:#2c2c2a;
            --base:#383835; --s1:#3987e5; --s2:#d95926; }}
  }}
  .bg {{ fill:var(--surface); }}
  .title {{ fill:var(--ink); font-size:14px; font-weight:600; }}
  .subtitle {{ fill:var(--ink2); font-size:12px; }}
  .label {{ fill:var(--ink2); font-size:12px; }}
  .value {{ fill:var(--ink); font-size:12px; font-variant-numeric:tabular-nums; }}
  .tick {{ fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }}
  .grid {{ stroke:var(--grid); stroke-width:1; }}
  .baseline {{ stroke:var(--base); stroke-width:1; }}
  .s1 {{ fill:var(--s1); }}
  .s2 {{ fill:var(--s2); }}
"""
_WIDTH: Final = 720
_BAR: Final = 16
_ROW: Final = 28
_RADIUS: Final = 4


@dataclass(frozen=True, slots=True)
class Bar:
    label: str
    value: float
    display: str


def compact(value: float) -> str:
    """1,284 / 12.9K / 4.2M style numbers."""
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{value / 1e9:.1f}B"
    if magnitude >= 1e6:
        return f"{value / 1e6:.1f}M"
    if magnitude >= 1e4:
        return f"{value / 1e3:.1f}K"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _fmt(value: float, *, percent: bool) -> str:
    return f"{value:.1%}" if percent else compact(value)


def nice_ticks(maximum: float, count: int = 4) -> list[float]:
    if maximum <= 0:
        return [0.0, 1.0]
    raw = maximum / count
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    ticks = [0.0]
    while ticks[-1] < maximum - 1e-9:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _hbar_path(x: float, y: float, width: float, height: float) -> str:
    if width <= 0:
        return ""
    r = min(_RADIUS, width, height / 2)
    right = x + width
    return (
        f"M{x:.1f},{y:.1f}H{right - r:.1f}Q{right:.1f},{y:.1f} {right:.1f},{y + r:.1f}"
        f"V{y + height - r:.1f}Q{right:.1f},{y + height:.1f} {right - r:.1f},{y + height:.1f}H{x:.1f}Z"
    )


def _column_path(x: float, base: float, width: float, height: float) -> str:
    if height <= 0:
        return ""
    r = min(_RADIUS, height, width / 2)
    top = base - height
    return (
        f"M{x:.1f},{base:.1f}V{top + r:.1f}Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f}"
        f"H{x + width - r:.1f}Q{x + width:.1f},{top:.1f} {x + width:.1f},{top + r:.1f}V{base:.1f}Z"
    )


def _open(height: float, title: str, subtitle: str) -> list[str]:
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="viz" viewBox="0 0 {_WIDTH} {height:.0f}" '
            f'width="{_WIDTH}" height="{height:.0f}" role="img" aria-labelledby="t d">'
        ),
        f'<title id="t">{escape(title)}</title><desc id="d">{escape(subtitle)}</desc>',
        f"<style>{_STYLE}</style>",
        f'<rect class="bg" x="0" y="0" width="{_WIDTH}" height="{height:.0f}" rx="8"/>',
        f'<text class="title" x="20" y="28">{escape(title)}</text>',
        f'<text class="subtitle" x="20" y="47">{escape(subtitle)}</text>',
    ]


def _label_width(labels: Sequence[str]) -> float:
    longest = max((len(label) for label in labels), default=4)
    return min(220.0, max(80.0, longest * 6.8 + 16))


def horizontal_bars(title: str, subtitle: str, bars: Sequence[Bar]) -> str:
    """Single-series ranked/ordered horizontal bars with the value at each tip."""
    top = 66.0
    label_width = _label_width([b.label for b in bars])
    left = 20 + label_width
    right_pad = 70.0
    plot_width = _WIDTH - left - right_pad
    maximum = max((b.value for b in bars), default=0.0)
    ticks = nice_ticks(maximum)
    scale = plot_width / ticks[-1] if ticks[-1] else 0.0
    height = top + len(bars) * _ROW + 34
    out = _open(height, title, subtitle)
    plot_bottom = top + len(bars) * _ROW
    for tick in ticks:
        x = left + tick * scale
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{top - 6:.1f}" x2="{x:.1f}" y2="{plot_bottom:.1f}"/>')
        out.append(
            f'<text class="tick" x="{x:.1f}" y="{plot_bottom + 16:.1f}" text-anchor="middle">{compact(tick)}</text>'
        )
    out.append(f'<line class="baseline" x1="{left:.1f}" y1="{top - 6:.1f}" x2="{left:.1f}" y2="{plot_bottom:.1f}"/>')
    for index, bar in enumerate(bars):
        y = top + index * _ROW + (_ROW - _BAR) / 2
        width = bar.value * scale
        out.append(
            f'<text class="label" x="{left - 8:.1f}" y="{y + _BAR - 4:.1f}" text-anchor="end">'
            f"{escape(bar.label)}</text>"
        )
        path = _hbar_path(left, y, width, _BAR)
        if path:
            out.append(f'<path class="s1" d="{path}"><title>{escape(bar.label)}: {escape(bar.display)}</title></path>')
        out.append(
            f'<text class="value" x="{left + width + 6:.1f}" y="{y + _BAR - 4:.1f}">{escape(bar.display)}</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def grouped_bars(
    title: str,
    subtitle: str,
    categories: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
) -> str:
    """Two-series horizontal bars (e.g. target vs achieved) with a legend."""
    if len(series) != 2:
        raise ValueError("grouped_bars supports exactly two series")
    top = 84.0
    row = 40.0
    bar = 12.0
    label_width = _label_width(categories)
    left = 20 + label_width
    plot_width = _WIDTH - left - 70.0
    maximum = max((v for _, values in series for v in values), default=0.0)
    ticks = nice_ticks(maximum)
    scale = plot_width / ticks[-1] if ticks[-1] else 0.0
    height = top + len(categories) * row + 34
    out = _open(height, title, subtitle)
    legend_x = 20.0
    for slot, (name, _) in enumerate(series, start=1):
        out.append(f'<rect class="s{slot}" x="{legend_x:.1f}" y="57" width="10" height="10" rx="2"/>')
        out.append(f'<text class="label" x="{legend_x + 16:.1f}" y="66">{escape(name)}</text>')
        legend_x += 16 + len(name) * 7 + 24
    plot_bottom = top + len(categories) * row
    for tick in ticks:
        x = left + tick * scale
        out.append(f'<line class="grid" x1="{x:.1f}" y1="{top - 6:.1f}" x2="{x:.1f}" y2="{plot_bottom:.1f}"/>')
        label = f"{tick:.0%}" if ticks[-1] <= 1 else compact(tick)
        out.append(f'<text class="tick" x="{x:.1f}" y="{plot_bottom + 16:.1f}" text-anchor="middle">{label}</text>')
    out.append(f'<line class="baseline" x1="{left:.1f}" y1="{top - 6:.1f}" x2="{left:.1f}" y2="{plot_bottom:.1f}"/>')
    for index, category in enumerate(categories):
        y0 = top + index * row + 6
        out.append(
            f'<text class="label" x="{left - 8:.1f}" y="{y0 + bar + 4:.1f}" text-anchor="end">{escape(category)}</text>'
        )
        for slot, (name, values) in enumerate(series, start=1):
            y = y0 + (slot - 1) * (bar + 2)  # 2px surface gap between the paired bars
            value = values[index]
            path = _hbar_path(left, y, value * scale, bar)
            display = f"{value:.1%}" if ticks[-1] <= 1 else compact(value)
            if path:
                out.append(
                    f'<path class="s{slot}" d="{path}">'
                    f"<title>{escape(category)} - {escape(name)}: {display}</title></path>"
                )
        best = max(values[index] for _, values in series)
        out.append(
            f'<text class="value" x="{left + best * scale + 6:.1f}" y="{y0 + bar + 4:.1f}">'
            f"{escape(' / '.join(_fmt(v[index], percent=ticks[-1] <= 1) for _, v in series))}</text>"
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def column_histogram(title: str, subtitle: str, bins: Sequence[tuple[str, float]], *, x_label: str) -> str:
    """Single-series histogram: columns grow from one baseline, no per-column labels."""
    top = 70.0
    left = 64.0
    plot_height = 180.0
    plot_width = _WIDTH - left - 24
    base = top + plot_height
    maximum = max((v for _, v in bins), default=0.0)
    ticks = nice_ticks(maximum)
    scale = plot_height / ticks[-1] if ticks[-1] else 0.0
    slot = plot_width / max(1, len(bins))
    width = min(24.0, slot - 2)
    height = base + 52
    out = _open(height, title, subtitle)
    for tick in ticks:
        y = base - tick * scale
        out.append(f'<line class="grid" x1="{left:.1f}" y1="{y:.1f}" x2="{left + plot_width:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{left - 8:.1f}" y="{y + 4:.1f}" text-anchor="end">{compact(tick)}</text>')
    out.append(f'<line class="baseline" x1="{left:.1f}" y1="{base:.1f}" x2="{left + plot_width:.1f}" y2="{base:.1f}"/>')
    label_every = max(1, math.ceil(len(bins) / 12))
    for index, (label, value) in enumerate(bins):
        x = left + index * slot + (slot - width) / 2
        path = _column_path(x, base, width, value * scale)
        if path:
            out.append(f'<path class="s1" d="{path}"><title>{escape(label)}: {compact(value)}</title></path>')
        if index % label_every == 0:
            out.append(
                f'<text class="tick" x="{x + width / 2:.1f}" y="{base + 16:.1f}" text-anchor="middle">'
                f"{escape(label)}</text>"
            )
    out.append(
        f'<text class="tick" x="{left + plot_width / 2:.1f}" y="{base + 38:.1f}" text-anchor="middle">'
        f"{escape(x_label)}</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"
