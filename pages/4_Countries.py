"""
Page 4 — Country View
Top producers, exporters, importers by commodity.
Always fetches live from the USDA FAS PSD bulk API (Snowflake only stores US + World).
"""

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

import style
from style import C_BLUE, C_GREEN, C_LIME, C_RED
from units import convert, unit_label

st.set_page_config(
    page_title="JSA WASDE — Countries",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="collapsed",
)
style.inject()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

COMM_CODES = {
    "Corn":        "0440000",
    "Soybeans":    "2222000",
    "Wheat":       "0410000",
    "Cotton":      "2631000",
    "Rice, Milled":"0422110",
    "Soybean Oil": "4232000",
}

ATTR_IDS = {
    "Production":    28,
    "Exports":       88,
    "Imports":       40,
    "Ending Stocks": 176,
    "Total Use":     185,
}

WORLD_CODES = {"0000US", "00World", "World", "00", "0000WO", "WorldTotal"}

PSD_BULK = "https://apps.fas.usda.gov/psdonline/api/data/get"

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


# ── Data ──────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_psd(comm_code: str) -> pd.DataFrame:
    """All years/countries/attributes for one commodity from the PSD bulk API."""
    try:
        resp = requests.get(
            PSD_BULK,
            params={"commodityCode": comm_code},
            timeout=90,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df.columns = [c.lower() for c in df.columns]
        # Normalize key column names
        df = df.rename(columns={
            next((c for c in df.columns if "attributeid"  in c), "attributeid"):  "attr_id",
            next((c for c in df.columns if "marketyear"   in c), "marketyear"):   "market_year",
            next((c for c in df.columns if "countrycode"  == c), "countrycode"):  "country_code",
            next((c for c in df.columns if "countryname"  == c), "countryname"):  "country_name",
            next((c for c in df.columns if c == "value"),         "value"):        "value",
        }, errors="ignore")
        for col in ("attr_id", "market_year", "value"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception:
        return pd.DataFrame()


def _build_ranking(comm_code: str, attr_id: int, year: int, n: int) -> pd.DataFrame:
    """
    Return top-N countries for `year` with Δ LY and Δ 5YA columns.
    All three values are drawn from the same bulk API call (latest vintage only).
    """
    raw = _fetch_psd(comm_code)
    if raw.empty or "attr_id" not in raw.columns:
        return pd.DataFrame()

    needed = {"attr_id", "market_year", "country_code", "value"}
    if not needed.issubset(raw.columns):
        return pd.DataFrame()

    sub = raw[raw["attr_id"] == attr_id].copy()
    sub = sub[~sub["country_code"].isin(WORLD_CODES)]
    sub = sub.dropna(subset=["market_year", "value"])

    # Wide pivot: country → year → value
    pivot = sub.pivot_table(
        index=["country_code", "country_name"] if "country_name" in sub.columns
              else ["country_code"],
        columns="market_year",
        values="value",
        aggfunc="first",
    )

    if year not in pivot.columns:
        return pd.DataFrame()

    cur = pivot[year].dropna()
    cur = cur[cur > 0].nlargest(n)

    rows = []
    for (code, *name_parts), val in cur.items():
        cname = name_parts[0] if name_parts else code

        cur_c = convert(val, commodity, imperial)

        # Δ LY
        ly_val = pivot.loc[(code, *name_parts), year - 1] if (year - 1) in pivot.columns else None
        delta_ly = (cur_c - convert(ly_val, commodity, imperial)) if ly_val is not None and not pd.isna(ly_val) else None

        # Δ 5YA: average of the 5 years before mkt_year
        five_yr_cols = [y for y in range(year - 5, year) if y in pivot.columns]
        five_yr_vals = [convert(pivot.loc[(code, *name_parts), y], commodity, imperial)
                        for y in five_yr_cols
                        if not pd.isna(pivot.loc[(code, *name_parts), y])]
        delta_5ya = (cur_c - sum(five_yr_vals) / len(five_yr_vals)) if five_yr_vals else None

        rows.append({
            "country_code": code,
            "country_name": cname,
            "current":      cur_c,
            "delta_ly":     delta_ly,
            "delta_5ya":    delta_5ya,
            "_raw":         val,  # for bar chart (pre-convert)
        })

    return pd.DataFrame(rows)


with st.spinner("Loading country data from USDA FAS…"):
    rank_df = _build_ranking(COMM_CODES[commodity], ATTR_IDS[attr_choice], mkt_year, top_n)

if rank_df.empty:
    st.warning(
        f"No country-level data for **{commodity} — {attr_choice}** in "
        f"{mkt_year}/{str(mkt_year+1)[-2:]}. USDA FAS may not publish this "
        "attribute for the selected combination, or data isn't available yet."
    )
    st.stop()

# ── Two-panel layout: chart left, ranking table right ─────────────────────────
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
        return f"{v:+,.1f}" if v is not None else "—"

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

    tbl = rank_df[["country_name", "current", "delta_ly", "delta_5ya"]].copy()
    tbl.insert(0, "#", range(1, len(tbl) + 1))
    tbl["current"]   = tbl["current"].map(lambda v: f"{v:,.1f}")
    tbl["delta_ly"]  = tbl["delta_ly"].map(_fmt_d)
    tbl["delta_5ya"] = tbl["delta_5ya"].map(_fmt_d)
    tbl.columns = ["#", "Country", f"Current ({ul})", "Δ LY", "Δ 5YA"]

    st.dataframe(
        tbl.style
           .applymap(_color_delta, subset=["Δ LY", "Δ 5YA"])
           .set_properties(**{"font-size": "0.85rem"}),
        use_container_width=True,
        hide_index=True,
        height=max(340, top_n * 36 + 40),
    )
    st.caption(
        f"Units: {ul}  ·  Source: USDA FAS PSD (latest vintage)  ·  "
        f"Δ LY = vs {mkt_year-1}/{str(mkt_year)[-2:]}  ·  Δ 5YA = vs {mkt_year-5}–{mkt_year-1} avg"
    )

# ── Side-by-side: producers vs exporters ──────────────────────────────────────
style.sec("Production vs Exports")
c1, c2 = st.columns(2)


@st.cache_data(ttl=3600, show_spinner=False)
def _side(comm_code, attr_id, year, n):
    return _build_ranking(comm_code, attr_id, year, n)


with c1:
    prod_df = _side(COMM_CODES[commodity], ATTR_IDS["Production"], mkt_year, 8)
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
    exp_df = _side(COMM_CODES[commodity], ATTR_IDS["Exports"], mkt_year, 8)
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
