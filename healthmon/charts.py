"""Gráficos SVG estáticos (sem dependências) para relatórios mensais/semanais."""
from __future__ import annotations

from datetime import date
from html import escape


def line_chart(title: str, points: list[tuple[date, float]], unit: str = "",
               band: tuple[float, float] | None = None, compare: list[tuple[date, float]] | None = None,
               width: int = 640, height: int = 200) -> str:
    if not points:
        return f'<figure class="chart"><figcaption>{escape(title)}: sem dados</figcaption></figure>'
    pad_l, pad_r, pad_t, pad_b = 44, 12, 24, 26
    vals = [v for _, v in points] + ([v for _, v in compare] if compare else [])
    if band:
        vals += list(band)
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    span = hi - lo
    lo, hi = lo - span * 0.08, hi + span * 0.08
    n = max(len(points), len(compare or [])) - 1 or 1

    def x(i: int) -> float:
        return pad_l + (width - pad_l - pad_r) * i / n

    def y(v: float) -> float:
        return pad_t + (height - pad_t - pad_b) * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}" '
             f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto">',
             f'<text x="{pad_l}" y="15" font-size="13" font-weight="600" fill="currentColor">{escape(title)}</text>']
    if band:
        parts.append(f'<rect x="{pad_l}" y="{y(band[1]):.1f}" width="{width - pad_l - pad_r}" '
                     f'height="{max(1, y(band[0]) - y(band[1])):.1f}" fill="#3b82f6" opacity="0.12"/>')
    for frac in (0, 0.5, 1):
        v = lo + (hi - lo) * frac
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                     f'stroke="currentColor" opacity="0.12"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y(v) + 4:.1f}" font-size="10" text-anchor="end" '
                     f'fill="currentColor" opacity="0.7">{v:.0f}</text>')
    if compare:
        pc = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(compare))
        parts.append(f'<polyline points="{pc}" fill="none" stroke="#9ca3af" stroke-width="1.5" '
                     f'stroke-dasharray="4 3"/>')
    pl = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(points))
    parts.append(f'<polyline points="{pl}" fill="none" stroke="#2563eb" stroke-width="2"/>')
    for i, (dd, v) in enumerate(points):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.2" fill="#2563eb">'
                     f'<title>{dd.strftime("%d/%m")}: {v:.1f} {escape(unit)}</title></circle>')
    parts.append(f'<text x="{pad_l}" y="{height - 6}" font-size="10" fill="currentColor" opacity="0.7">'
                 f'{points[0][0].strftime("%d/%m")}</text>')
    parts.append(f'<text x="{width - pad_r}" y="{height - 6}" font-size="10" text-anchor="end" '
                 f'fill="currentColor" opacity="0.7">{points[-1][0].strftime("%d/%m")}</text>')
    parts.append("</svg>")
    legend = "Linha azul: período atual"
    if compare:
        legend += "; tracejado cinza: período anterior"
    if band:
        legend += "; faixa azul: sua faixa usual"
    return f'<figure class="chart">{"".join(parts)}<figcaption>{legend}</figcaption></figure>'
