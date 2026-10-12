"""
Page 4 — Country View
Top producers, exporters, importers by commodity.
Uses only the Viz API (confirmed working from Streamlit Cloud).
Bulk CSV ZIP downloads are blocked; all data comes from GetAllAttributeData.
"""

import re

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import pathlib
import sys


def _fresh_modules():
    # Streamlit Cloud re-reads scripts on a push but keeps already-imported modules
    # (db, style, ...), so pushed fixes to them don't run until a Reboot.
    here = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "style.py").exists())
    seen = tuple(sorted((p.name, p.stat().st_mtime_ns) for p in here.glob("*.py")))
    if getattr(sys, "_wasde_code", None) == seen:
        return
    for name, mod in list(sys.modules.items()):
        path = getattr(mod, "__file__", None)
        if name != "__main__" and path and pathlib.Path(path).resolve().parent == here:
            sys.modules.pop(name, None)
    st.cache_data.clear()
    sys._wasde_code = seen


_fresh_modules()

import style
from style import C_BLUE, C_GREEN, C_LIME, C_RED
from units import convert, unit_label

st.set_page_config(
    page_title="JSA WASDE — Countries",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

COMM_CODES = {
    "Corn":         "0440000",
    "Soybeans":     "2222000",
    "Wheat":        "0410000",
    "Cotton":       "2631000",
    "Rice, Milled": "0422110",
    "Soybean Oil":  "4232000",
}

# Attribute IDs as strings (matched against viz API response attributeId field)
ATTR_IDS = {
    "Production":    "28",
    "Exports":       "88",
    "Imports":       "57",
    "Ending Stocks": "176",
    "Consumption":   "125",
}

PSD_VIZ_BASE = "https://apps.fas.usda.gov/PSDOnlineApi/api/CompositeVisualization"

# Display names for country codes used in curated universes
COUNTRY_NAMES = {
    "US": "United States",  "BR": "Brazil",         "AR": "Argentina",
    "CH": "China",          "E4": "European Union",  "UP": "Ukraine",
    "IN": "India",          "MX": "Mexico",          "RS": "Russia",
    "SF": "South Africa",   "CA": "Canada",          "EG": "Egypt",
    "NG": "Nigeria",        "TH": "Thailand",        "KZ": "Kazakhstan",
    "TZ": "Tanzania",       "ET": "Ethiopia",        "PH": "Philippines",
    "PA": "Paraguay",       "BO": "Bolivia",         "UR": "Uruguay",
    "ZA": "Zambia",         "AS": "Australia",       "TU": "Turkey",
    "PK": "Pakistan",       "IR": "Iran",            "MO": "Morocco",
    "KS": "Korea, South",   "UK": "United Kingdom",  "UZ": "Uzbekistan",
    "ML": "Mali",           "BM": "Burma",           "JA": "Japan",
    "BD": "Bangladesh",     "VM": "Vietnam",         "ID": "Indonesia",
    "KN": "Korea, North",   "MY": "Malaysia",        "TX": "Turkmenistan",
}

# Curated country universe per commodity — enough to capture top 15 for any attribute
COUNTRY_UNIVERSE = {
    "Corn": [
        "US", "BR", "AR", "CH", "E4", "UP", "IN", "MX", "RS",
        "SF", "CA", "EG", "NG", "TH", "KZ", "TZ", "ET", "PH",
    ],
    "Soybeans": [
        "US", "BR", "AR", "CH", "IN", "CA", "PA", "RS", "UP",
        "BO", "E4", "UR", "ZA",
    ],
    "Wheat": [
        "CH", "E4", "IN", "RS", "US", "CA", "AS", "AR", "KZ",
        "TU", "UP", "PK", "IR", "MO", "EG", "KS", "UK", "BR",
    ],
    "Cotton": [
        "IN", "CH", "US", "PK", "BR", "AS", "TU", "UZ", "AR",
        "UP", "ML", "BM", "TX",
    ],
    "Rice, Milled": [
        "CH", "IN", "ID", "TH", "VM", "BM", "BD", "BR", "PH",
        "JA", "PK", "NG", "KS", "US", "KN", "TZ",
    ],
    "Soybean Oil": [
        "CH", "IN", "E4", "AR", "BR", "US", "ID", "MY", "RS",
        "PK", "TH", "KZ",
    ],
}

# ── Controls ──────────────────────────────────────────────────────────────────
st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2, _c3, _c4, _c5 = st.columns([2, 1, 1, 1, 1])
with _c1:
    commodity = st.selectbox("Commodity", COMMODITIES)
with _c2:
    imperial = st.toggle("Million bu", value=False)
with _c3:
    mkt_year = st.selectbox("Mkt Year", list(range(2026, 2004, -1)), index=0,
                             format_func=lambda y: f"{y}/{str(y+1)[-2:]}")
with _c4:
    attr_choice = st.selectbox("Attribute", list(ATTR_IDS.keys()))
with _c5:
    top_n = st.slider("Top N", 5, 20, 10)
st.markdown("</div>", unsafe_allow_html=True)

ul = unit_label(commodity, imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{commodity} — Country Ranking</div>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<span style='font-size:0.95rem;color:{style.TEXT_MUTED};font-weight:400'>"
    f"Top countries by {attr_choice} &nbsp;·&nbsp; {mkt_year}/{str(mkt_year+1)[-2:]}</span>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)


# ── Reporting periods ─────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _get_periods() -> tuple:
    try:
        r = requests.get(f"{PSD_VIZ_BASE}/GetReportingPeriods", timeout=20,
                         headers={"User-Agent": "JPSI-WASDE/1.0"})
        r.raise_for_status()
        periods = r.json()
        latest = periods[0]["value"]
        prior  = periods[1]["value"] if len(periods) > 1 else latest
        return latest, prior
    except Exception:
        from datetime import datetime
        now = datetime.now()
        cur = f"{now.year}{now.month:02d}"
        pm  = now.month - 1 if now.month > 1 else 12
        py  = now.year if now.month > 1 else now.year - 1
        return cur, f"{py}{pm:02d}"


rp, rp_prior = _get_periods()
prior_yr  = int(rp_prior[:4])
prior_mo  = int(rp_prior[4:])
prior_lbl = pd.Timestamp(year=prior_yr, month=prior_mo, day=1).strftime("%b %Y")


# ── Viz API helpers ───────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_viz(comm_code: str, country_code: str, period: str) -> dict:
    try:
        r = requests.post(
            f"{PSD_VIZ_BASE}/GetAllAttributeData",
            json={
                "reportingPeriod":       period,
                "reportingCutoffPeriod": None,
                "commodityCode":         comm_code,
                "regionCode":            "R00",
                "countryCode":           country_code,
                "attributeId":           "4",
            },
            timeout=30,
            headers={"User-Agent": "JPSI-WASDE/1.0"},
        )
        r.raise_for_status()
        return r.json() or {}
    except Exception:
        return {}


def _parse_attr(data: dict, attr_id_str: str) -> dict:
    """Return a dict {year: value, 'delta_lr': raw_change} for a given attribute."""
    result = {}
    headers = data.get("headerValues", [])
    for attr in data.get("attributeValues", []):
        if str(attr.get("attributeId", "")).strip() != attr_id_str:
            continue
        vals = attr.get("attributeValues", [])
        for col_i, hdr in enumerate(headers[1:]):
            hdr = str(hdr).strip()
            # The Change column (col_i=1) gives Δ LR for the current year
            if col_i == 1:
                raw = vals[col_i] if col_i < len(vals) else None
                if raw and raw not in ("", "-"):
                    # format: "+5949(+1.48%)" — extract just the leading number
                    m = re.match(r"([+-]?\d[\d,]*)", str(raw).replace(" ", ""))
                    if m:
                        try:
                            result["delta_lr_raw"] = float(m.group(1).replace(",", ""))
                        except ValueError:
                            pass
                continue
            # Skip the prior-period repeat column (col_i=2, same year as col_i=0)
            if col_i == 2:
                continue
            m = re.match(r"(\d{2})/\d{2}", hdr)
            if not m or col_i >= len(vals):
                continue
            myr = 2000 + int(m.group(1))
            raw = vals[col_i]
            if raw is None or raw == "" or raw == "-":
                continue
            try:
                result[myr] = float(str(raw).split("(")[0].strip().replace(",", ""))
            except ValueError:
                pass
        break  # found our attribute
    return result


def _country_name_from_viz(country_code: str) -> str:
    return COUNTRY_NAMES.get(country_code, country_code)


def _build_ranking_viz(comm_code: str, attr_id_str: str, year: int,
                        n: int, countries: list, commodity_name: str,
                        use_imperial: bool) -> pd.DataFrame:
    """Build ranking table entirely from viz API calls (no ZIP download)."""
    rows = []
    for cc in countries:
        data   = _fetch_viz(comm_code, cc, rp)
        parsed = _parse_attr(data, attr_id_str)

        cur_raw = parsed.get(year)
        if cur_raw is None or cur_raw <= 0:
            continue

        cname   = _country_name_from_viz(cc)
        cur_c   = convert(cur_raw, commodity_name, use_imperial)

        # Δ LR — already in the Change column of the current-period response
        dlr_raw  = parsed.get("delta_lr_raw")
        delta_lr = convert(dlr_raw, commodity_name, use_imperial) if dlr_raw is not None else None

        # Δ LY
        ly_raw   = parsed.get(year - 1)
        delta_ly = (cur_c - convert(ly_raw, commodity_name, use_imperial)) if ly_raw is not None else None

        # Δ 5YA
        five_vals = [convert(parsed[y], commodity_name, use_imperial)
                     for y in range(year - 5, year) if y in parsed]
        delta_5ya = (cur_c - sum(five_vals) / len(five_vals)) if five_vals else None

        rows.append({
            "country_code": cc,
            "country_name": cname,
            "current":      cur_c,
            "delta_lr":     delta_lr,
            "delta_ly":     delta_ly,
            "delta_5ya":    delta_5ya,
        })

    if not rows:
        return pd.DataFrame()

    return (pd.DataFrame(rows)
              .sort_values("current", ascending=False)
              .head(n)
              .reset_index(drop=True))


# ── Load data ─────────────────────────────────────────────────────────────────
countries = COUNTRY_UNIVERSE.get(commodity, list(COUNTRY_UNIVERSE["Corn"]))

with st.spinner(f"Loading {commodity} country data from USDA FAS…"):
    rank_df = _build_ranking_viz(
        COMM_CODES[commodity], ATTR_IDS[attr_choice], mkt_year,
        top_n, countries, commodity, imperial,
    )

if rank_df.empty:
    st.warning(
        f"No data returned for **{commodity} — {attr_choice}** in "
        f"{mkt_year}/{str(mkt_year+1)[-2:]}. "
        "USDA FAS may not have data for this combination yet."
    )
    st.stop()


# ── Layout: chart left, ranking table right ───────────────────────────────────
col_chart, col_table = st.columns([1.1, 1])

with col_chart:
    style.sec(f"Top {top_n} — {attr_choice}")
    fig_top = go.Figure(go.Bar(
        x=rank_df["current"].tolist()[::-1],
        y=rank_df["country_name"].tolist()[::-1],
        orientation="h",
        marker_color=C_GREEN,
        hovertemplate="%{y}: %{x:,.1f}<extra></extra>",
    ))
    fig_top.update_layout(**style.plot_layout(
        title=dict(
            text=f"{commodity} — Top {top_n} {attr_choice} ({mkt_year}/{str(mkt_year+1)[-2:]})",
            font=dict(size=13),
        ),
        xaxis_title=ul,
        height=max(320, top_n * 34),
        margin=dict(l=150, r=8, t=36, b=8),
    ))
    st.plotly_chart(fig_top, use_container_width=True)

with col_table:
    style.sec("Ranking Table")

    def _fmt_d(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return "—"
        return f"{v:+,.1f}"

    def _color_delta(val):
        if not isinstance(val, str) or val == "—":
            return ""
        try:
            n = float(val.replace(",", ""))
            if n > 0: return f"color:{C_GREEN};font-weight:600"
            if n < 0: return f"color:{C_RED};font-weight:600"
        except ValueError:
            pass
        return ""

    tbl = rank_df[["country_name", "current", "delta_lr", "delta_ly", "delta_5ya"]].copy()
    tbl.insert(0, "#", range(1, len(tbl) + 1))
    tbl["current"]   = tbl["current"].map(lambda v: f"{v:,.1f}")
    tbl["delta_lr"]  = tbl["delta_lr"].map(_fmt_d)
    tbl["delta_ly"]  = tbl["delta_ly"].map(_fmt_d)
    tbl["delta_5ya"] = tbl["delta_5ya"].map(_fmt_d)
    tbl.columns = ["#", "Country", f"Current ({ul})", "Δ LR", "Δ LY", "Δ 5YA"]

    delta_cols = ["Δ LR", "Δ LY", "Δ 5YA"]
    st.dataframe(
        tbl.style
           .map(_color_delta, subset=delta_cols)
           .set_properties(**{"font-size": "0.85rem"}),
        use_container_width=True,
        hide_index=True,
        height=max(360, top_n * 36 + 40),
    )
    st.caption(
        f"Units: {ul}  ·  Source: USDA FAS PSD  ·  "
        f"Δ LR = vs {prior_lbl} WASDE  ·  "
        f"Δ LY = vs {mkt_year-1}/{str(mkt_year)[-2:]}  ·  "
        f"Δ 5YA = vs {mkt_year-5}–{mkt_year-1} avg"
    )


# ── Side-by-side: producers vs exporters ──────────────────────────────────────
style.sec("Production vs Exports")
c1, c2 = st.columns(2)


@st.cache_data(ttl=3600, show_spinner=False)
def _side_viz(comm_code, attr_id_str, year, n, commodity_key, use_imperial):
    ctries = COUNTRY_UNIVERSE.get(commodity_key, list(COUNTRY_UNIVERSE["Corn"]))
    return _build_ranking_viz(comm_code, attr_id_str, year, n, ctries, commodity_key, use_imperial)


with c1:
    prod_df = _side_viz(COMM_CODES[commodity], ATTR_IDS["Production"], mkt_year, 8, commodity, imperial)
    if not prod_df.empty:
        fig_p = go.Figure(go.Bar(
            x=prod_df["current"].tolist()[::-1],
            y=prod_df["country_name"].tolist()[::-1],
            orientation="h", marker_color=C_LIME,
        ))
        fig_p.update_layout(**style.plot_layout(
            title=dict(text="Top 8 Producers", font=dict(size=13)),
            xaxis_title=ul, height=300,
            margin=dict(l=130, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_p, use_container_width=True)

with c2:
    exp_df = _side_viz(COMM_CODES[commodity], ATTR_IDS["Exports"], mkt_year, 8, commodity, imperial)
    if not exp_df.empty:
        fig_e = go.Figure(go.Bar(
            x=exp_df["current"].tolist()[::-1],
            y=exp_df["country_name"].tolist()[::-1],
            orientation="h", marker_color=C_BLUE,
        ))
        fig_e.update_layout(**style.plot_layout(
            title=dict(text="Top 8 Exporters", font=dict(size=13)),
            xaxis_title=ul, height=300,
            margin=dict(l=130, r=8, t=36, b=8),
        ))
        st.plotly_chart(fig_e, use_container_width=True)

style.disclaimer()
