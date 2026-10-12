"""
Page 4 — Country View
Top producers, exporters, importers by commodity.
Bulk API  → ranking + Δ LY + Δ 5YA (all countries, latest vintage only).
Viz API   → Δ LR per top-N country (current vs prior WASDE reporting period).
"""

import io
import re
import zipfile

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
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

COMMODITIES = ["Corn", "Soybeans", "Wheat", "Cotton", "Rice, Milled", "Soybean Oil"]

COMM_CODES = {
    "Corn":        "0440000",
    "Soybeans":    "2222000",
    "Wheat":       "0410000",
    "Cotton":      "2631000",
    "Rice, Milled":"0422110",
    "Soybean Oil": "4232000",
}

# Integer attr IDs used by the bulk API; string forms match the viz API response
ATTR_IDS = {
    "Production":    28,
    "Exports":       88,
    "Imports":       40,
    "Ending Stocks": 176,
    "Total Use":     185,
}

WORLD_CODES = {"0000US", "00World", "World", "00", "0000WO", "WorldTotal"}

PSD_BULK_ZIP = "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"
PSD_OILSEEDS_ZIP = "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip"
PSD_VIZ_BASE = "https://apps.fas.usda.gov/PSDOnlineApi/api/CompositeVisualization"

COMM_ZIP = {
    "Corn":         PSD_BULK_ZIP,
    "Soybeans":     PSD_OILSEEDS_ZIP,
    "Wheat":        PSD_BULK_ZIP,
    "Cotton":       "https://apps.fas.usda.gov/psdonline/downloads/psd_cotton_csv.zip",
    "Rice, Milled": PSD_BULK_ZIP,
    "Soybean Oil":  PSD_OILSEEDS_ZIP,
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
def _get_periods() -> tuple[str, str]:
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


# ── Bulk CSV ZIP: ranking + Δ LY + Δ 5YA ────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_psd(comm_code: str, zip_url: str) -> pd.DataFrame:
    """Download USDA FAS PSD CSV ZIP and return long-form DataFrame for one commodity."""
    try:
        resp = requests.get(zip_url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            # Pick the first CSV file inside the ZIP
            csv_name = next((n for n in z.namelist() if n.endswith(".csv")), None)
            if not csv_name:
                return pd.DataFrame()
            with z.open(csv_name) as f:
                df = pd.read_csv(f, low_memory=False)
        df.columns = [c.strip() for c in df.columns]
        # Normalise column names
        col_map = {}
        for c in df.columns:
            cl = c.lower().replace("_", "").replace(" ", "")
            if "commoditycode" in cl:   col_map[c] = "commodity_code"
            elif "attributeid" in cl:   col_map[c] = "attr_id"
            elif "marketyear" in cl:    col_map[c] = "market_year"
            elif "countrycode" in cl:   col_map[c] = "country_code"
            elif "countryname" in cl:   col_map[c] = "country_name"
            elif cl == "value":         col_map[c] = "value"
        df = df.rename(columns=col_map)
        for col in ("attr_id", "market_year", "value"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        # Filter to just this commodity
        if "commodity_code" in df.columns:
            df = df[df["commodity_code"].astype(str).str.strip() == str(comm_code)]
        return df
    except Exception:
        return pd.DataFrame()


def _build_ranking(comm_code: str, attr_id: int, year: int, n: int, zip_url: str) -> pd.DataFrame:
    raw = _fetch_psd(comm_code, zip_url)
    if raw.empty or "attr_id" not in raw.columns:
        return pd.DataFrame()

    needed = {"attr_id", "market_year", "country_code", "value"}
    if not needed.issubset(raw.columns):
        return pd.DataFrame()

    sub = raw[raw["attr_id"] == attr_id].copy()
    sub = sub[~sub["country_code"].isin(WORLD_CODES)]
    sub = sub.dropna(subset=["market_year", "value"])

    idx_cols = (["country_code", "country_name"]
                if "country_name" in sub.columns else ["country_code"])
    pivot = sub.pivot_table(index=idx_cols, columns="market_year",
                            values="value", aggfunc="first")

    if year not in pivot.columns:
        return pd.DataFrame()

    cur = pivot[year].dropna()
    cur = cur[cur > 0].nlargest(n)

    rows = []
    for idx_key, val in cur.items():
        if isinstance(idx_key, tuple):
            code, cname = idx_key[0], idx_key[1] if len(idx_key) > 1 else idx_key[0]
        else:
            code, cname = idx_key, idx_key

        cur_c = convert(val, commodity, imperial)

        ly_val = pivot.loc[idx_key, year - 1] if (year - 1) in pivot.columns else None
        delta_ly = (cur_c - convert(ly_val, commodity, imperial)) \
            if (ly_val is not None and not pd.isna(ly_val)) else None

        five_yr_cols = [y for y in range(year - 5, year) if y in pivot.columns]
        five_yr_vals = [convert(pivot.loc[idx_key, y], commodity, imperial)
                        for y in five_yr_cols if not pd.isna(pivot.loc[idx_key, y])]
        delta_5ya = (cur_c - sum(five_yr_vals) / len(five_yr_vals)) if five_yr_vals else None

        rows.append({
            "country_code": code,
            "country_name": cname,
            "current":      cur_c,
            "delta_lr":     None,   # filled in below
            "delta_ly":     delta_ly,
            "delta_5ya":    delta_5ya,
        })

    return pd.DataFrame(rows)


# ── Viz API: Δ LR per country ─────────────────────────────────────────────────
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


def _parse_attr_value(data: dict, attr_id_str: str, year: int) -> float | None:
    """Extract one attribute+year value from a GetAllAttributeData response."""
    headers = data.get("headerValues", [])
    for attr in data.get("attributeValues", []):
        if str(attr.get("attributeId", "")).strip() != attr_id_str:
            continue
        vals = attr.get("attributeValues", [])
        for col_i, hdr in enumerate(headers[1:]):
            if col_i in (1, 2):
                continue
            m = re.match(r"(\d{2})/\d{2}", hdr.strip())
            if not m or col_i >= len(vals):
                continue
            myr = 2000 + int(m.group(1))
            if myr != year:
                continue
            raw = vals[col_i]
            if raw is None or raw == "":
                return None
            try:
                return float(str(raw).split("(")[0].strip().replace(",", ""))
            except ValueError:
                return None
    return None


def _add_delta_lr(rank_df: pd.DataFrame, comm_code: str,
                  attr_id: int, year: int) -> pd.DataFrame:
    """Fetch prior-period viz data for each country and compute Δ LR."""
    if rank_df.empty or rp == rp_prior:
        return rank_df

    attr_id_str = str(attr_id)
    for i, row in rank_df.iterrows():
        cc   = row["country_code"]
        data = _fetch_viz(comm_code, cc, rp_prior)
        prior_val = _parse_attr_value(data, attr_id_str, year)
        if prior_val is not None:
            prior_c = convert(prior_val, commodity, imperial)
            rank_df.at[i, "delta_lr"] = row["current"] - prior_c

    return rank_df


# ── Load data ─────────────────────────────────────────────────────────────────
zip_url = COMM_ZIP[commodity]

with st.spinner("Loading country data from USDA FAS…"):
    rank_df = _build_ranking(COMM_CODES[commodity], ATTR_IDS[attr_choice], mkt_year, top_n, zip_url)

if rank_df.empty:
    raw_debug = _fetch_psd(COMM_CODES[commodity], zip_url)
    if raw_debug.empty:
        st.error("PSD CSV download returned no data — ZIP may be unreachable. Try again shortly.")
    else:
        avail_attrs = sorted(raw_debug["attr_id"].dropna().unique().tolist()) if "attr_id" in raw_debug.columns else []
        avail_yrs   = sorted(raw_debug["market_year"].dropna().unique().tolist(), reverse=True)[:5] if "market_year" in raw_debug.columns else []
        st.warning(
            f"No country-level data for **{commodity} — {attr_choice}** "
            f"(attr_id={ATTR_IDS[attr_choice]}) in {mkt_year}/{str(mkt_year+1)[-2:]}. "
            f"Available attr IDs: {avail_attrs[:15]}. "
            f"Available years: {avail_yrs}. "
            f"Columns: {list(raw_debug.columns)}."
        )
    st.stop()

with st.spinner(f"Fetching {prior_lbl} WASDE for Δ LR…"):
    rank_df = _add_delta_lr(rank_df, COMM_CODES[commodity],
                            ATTR_IDS[attr_choice], mkt_year)


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
def _side(comm_code, attr_id, year, n, zip_url):
    return _build_ranking(comm_code, attr_id, year, n, zip_url)


with c1:
    prod_df = _side(COMM_CODES[commodity], ATTR_IDS["Production"], mkt_year, 8, zip_url)
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
    exp_df = _side(COMM_CODES[commodity], ATTR_IDS["Exports"], mkt_year, 8, zip_url)
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
