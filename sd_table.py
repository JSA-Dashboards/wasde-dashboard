"""Clean supply/demand table for agency balance sheets (CONAB), styled like the Balance Sheet page."""
from __future__ import annotations

import html

import numpy as np

BOLD = {"Total Supply", "Total Use", "Ending Stocks"}


def _fmt(v, dec: int, signed: bool, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    s = f"{abs(v):,.{dec}f}" if signed else f"{v:,.{dec}f}"
    if signed:
        s = ("+" if v > 0 else "−" if v < 0 else "") + s
    return s + ("%" if pct else "")


def render(rows: list[tuple], seasons: list[str], cur: str, unit: str, colors: dict, signed: bool = False,
           trend: bool = False, bold: set | None = None, heading: str | None = None,
           title: tuple[str, str] | None = None) -> str:
    """rows = [(label, kind, {season: value}[, decimals]), ...]; kind in {'qty', 'pct'}.

    seasons are shown in the order given. trend=True colours each value by its change from the previous column
    (the `cur` column is left plain). bold = row labels to emphasise (default: Total Supply / Total Use / Ending Stocks)."""
    bold = BOLD if bold is None else bold
    head = "".join(f"<th class='{'cur' if s == cur else ''}'>{html.escape(s)}</th>" for s in seasons)
    body = [f"<tr class='grp'><td colspan='{len(seasons) + 1}'>{html.escape(heading or 'Balance sheet')} "
            f"<span class='gu'>· {html.escape(unit)}</span></td></tr>"]
    for row in rows:
        label, kind, vals = row[:3]
        style = "ratio" if kind == "pct" else "total" if label in bold else ""
        dec = row[3] if len(row) > 3 and row[3] is not None else (
            1 if kind == "pct" else (1 if max([abs(v) for v in vals.values() if v is not None and v == v] or [0]) < 100 else 0))
        cells = []
        prev = None
        for s in seasons:
            x = vals.get(s)
            cls = "cur" if s == cur else ""
            if signed and isinstance(x, (int, float)) and x == x and round(x, dec) != 0:
                cls += " pos" if x > 0 else " neg"
            if trend and s != cur and isinstance(x, (int, float)) and x == x:
                if prev is not None and round(x, dec) != round(prev, dec):
                    cls += " pos" if x > prev else " neg"
                prev = x
            cells.append(f"<td class='{cls.strip()}'>{_fmt(x, dec, signed, kind == 'pct')}</td>")
        body.append(f"<tr class='{style}'><th class='lab'>{html.escape(label)}</th>{''.join(cells)}</tr>")
    css = (
        "<style>"
        ".bs-wrap{overflow-x:auto;border:1px solid %(border)s;border-radius:10px;background:#fff;margin-bottom:6px}"
        ".bs-title{position:sticky;left:0;padding:14px 16px 10px;background:#fff;border-bottom:3px solid %(green)s}"
        ".bs-title .t1{font:700 1.4rem/1.15 'EB Garamond',Georgia,serif;color:%(text)s}"
        ".bs-title .t2{font:700 0.95rem 'Source Sans 3','Source Sans Pro',Arial,sans-serif;color:%(green)s;margin-top:3px}"
        "table.bs{border-collapse:separate;border-spacing:0;width:100%%;font-size:0.88rem;"
        "font-variant-numeric:tabular-nums;color:%(text)s}"
        "table.bs thead th{background:%(green)s;color:#fff;font-weight:600;padding:8px 12px;"
        "text-align:right;white-space:nowrap}"
        "table.bs thead th:first-child{position:sticky;left:0;z-index:3;text-align:left;min-width:190px}"
        "table.bs thead th.cur{background:%(dark)s}"
        "table.bs td,table.bs th.lab{padding:6px 12px;text-align:right;white-space:nowrap;"
        "border-bottom:1px solid %(border)s}"
        "table.bs th.lab{position:sticky;left:0;background:#fff;text-align:left;font-weight:500;z-index:1}"
        "table.bs .gu{font-weight:400;letter-spacing:0;text-transform:none;font-size:0.72rem;color:%(muted)s;margin-left:4px}"
        "table.bs td.cur{background:#fbf7e8}"
        "table.bs tr.grp td{background:#eef3ef;color:%(green)s;font-size:0.7rem;font-weight:700;"
        "letter-spacing:0.12em;text-transform:uppercase;text-align:left;padding:5px 12px}"
        "table.bs tr.total td,table.bs tr.total th.lab{font-weight:700;background:#f4f7f5;"
        "border-top:1.5px solid %(green)s}"
        "table.bs tr.total td.cur{background:#f6f0d8}"
        "table.bs tr.ratio td,table.bs tr.ratio th.lab{font-weight:700;color:%(good)s;background:#eef6ee}"
        "table.bs td.pos{color:%(good)s;font-weight:600}table.bs td.neg{color:%(bad)s;font-weight:600}"
        "table.bs tr:last-child td,table.bs tr:last-child th.lab{border-bottom:none}"
        "</style>"
    ) % {k: colors[k] for k in ("border", "text", "green", "dark", "muted", "good", "bad")}
    title_html = ""
    if title:
        title_html = (f"<div class='bs-title'><div class='t1'>{html.escape(title[0])}</div>"
                      f"<div class='t2'>{html.escape(title[1])}</div></div>")
    return (f"{css}<div class='bs-wrap'>{title_html}<table class='bs'><thead><tr><th>Category</th>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")
