"""
Page 7 — Alternative Groups: origin crop estimates
Brazil: CONAB historical series (area / yield / production by state), loaded to Snowflake by
etl/fetch_south_america.py.
Argentina: official MAGyP campaign history. Ukraine / Russia / Australia: USDA PSD baseline.
Agency estimates with no automated feed (BAGE, BCR, UGA, SovEcon, IKAR, ABARES, ...) are typed in on
each tab (password-gated) and stored in JSA.WASDE.AGENCY_ESTIMATES.
"""

import hmac
import os
from urllib.parse import quote
from datetime import date

import pandas as pd
import plotly.graph_objects as go
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

import db  # noqa: E402
import psd  # noqa: E402
import balance_sheet as bs  # noqa: E402
import psd_meta as pm  # noqa: E402
import sd_table  # noqa: E402
import style  # noqa: E402
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_ORANGE, C_RED  # noqa: E402
from units import convert, unit_label  # noqa: E402

st.set_page_config(
    page_title="JSA WASDE — Alternative Groups",
    page_icon="🌎",
    layout="wide",
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

CROPS = ["Soybeans", "Corn", "Wheat", "Barley", "Canola"]
_CORE = ("Soybeans", "Corn", "Wheat")   # CONAB / MAGyP coverage
_MT_TO_BU = {"Corn": 39.368, "Soybeans": 36.744, "Wheat": 36.744, "Barley": 45.93, "Canola": 44.09}
HA_PER_ACRE = 0.404686

UF_NAMES = {
    "RR": "Roraima", "RO": "Rondônia", "AC": "Acre", "AM": "Amazonas", "AP": "Amapá",
    "PA": "Pará", "TO": "Tocantins", "MA": "Maranhão", "PI": "Piauí", "CE": "Ceará",
    "RN": "Rio Grande do Norte", "PB": "Paraíba", "PE": "Pernambuco", "AL": "Alagoas",
    "SE": "Sergipe", "BA": "Bahia", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "GO": "Goiás", "DF": "Distrito Federal", "MG": "Minas Gerais", "ES": "Espírito Santo",
    "RJ": "Rio de Janeiro", "SP": "São Paulo", "PR": "Paraná", "SC": "Santa Catarina",
    "RS": "Rio Grande do Sul",
}


def _secret(key: str) -> str:
    try:
        return str(st.secrets[key])
    except Exception:
        return os.environ.get(key, "")


def fmt_d(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{v:+,.1f}"


def color_delta(val):
    if not isinstance(val, str) or val == "—":
        return ""
    try:
        n = float(val.replace(",", ""))
    except ValueError:
        return ""
    return f"color:{C_GREEN};font-weight:600" if n > 0 else f"color:{C_RED};font-weight:600" if n < 0 else ""


def cvt(kt, crop, imperial):
    """1,000 MT -> display unit (million bu for soybeans / corn / wheat; barley and canola stay metric)."""
    return convert(kt, crop, imperial and crop in _CORE)


def yld(kg_ha, crop, imperial):
    """kg/ha -> t/ha (metric) or bu/ac (imperial)."""
    if imperial and crop in _CORE:
        return kg_ha / 1000 * _MT_TO_BU[crop] * HA_PER_ACRE
    return kg_ha / 1000


def yld_unit(imperial):
    return "bu/ac" if imperial and crop in _CORE else "t/ha"


# ── Controls ──────────────────────────────────────────────────────────────────
st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2 = st.columns([2, 1])
with _c1:
    crop = st.selectbox("Crop", CROPS)
with _c2:
    imperial = st.toggle("Million bu", value=False)
st.markdown("</div>", unsafe_allow_html=True)

ul = unit_label(crop, imperial) if crop in _CORE else "1,000 MT"
yu = yld_unit(imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{crop} — Alternative groups: crop estimates</div>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info("**Snowflake not configured.** This page reads CONAB_SERIES / MAGYP_SERIES / AGENCY_ESTIMATES.")
    style.disclaimer()
    st.stop()


# ── Brazil · CONAB ────────────────────────────────────────────────────────────
CORN_CROPS = {"Total": "Corn", "1st crop": "Corn (1st crop)", "2nd crop (safrinha)": "Corn (2nd crop)",
              "3rd crop": "Corn (3rd crop)"}

@st.fragment
def brazil_tab():
    if crop not in _CORE:
        st.info("CONAB series are loaded for soybeans, corn and wheat.")
        return
    series_crop, what = crop, crop.lower()
    if crop == "Corn":
        pick = st.segmented_control("Corn crop", list(CORN_CROPS), default="Total", key="br_corn_crop") or "Total"
        series_crop = CORN_CROPS[pick]
        what = "corn" if pick == "Total" else f"corn, {pick}"
    df = db.conab_series(series_crop)
    if df.empty:
        st.info("No CONAB data loaded yet. Run `python etl/fetch_south_america.py`.")
        return

    tot = df[df.level == "total"]

    def series(metric):
        s = tot[tot.metric == metric].sort_values("start_year")
        return s.set_index("start_year")["value"], s.set_index("start_year")["label"], \
            s.set_index("start_year")["is_forecast"]

    prod, labels, fc = series("production_kt")
    area, _, _ = series("area_kha")
    yl, _, _ = series("yield_kgha")
    last = prod.index.max()
    lab = labels[last] + (" (CONAB estimate)" if bool(fc[last]) else "")

    style.sec(f"Brazil national — {lab}")
    k1, k2, k3 = st.columns(3)

    def kpi(col, title, cur, prev, fiveavg, fmt, unit):
        with col:
            if cur is None:
                st.metric(title, "N/A")
                return
            delta = None
            if prev is not None:
                delta = f"{cur - prev:+,.2f} vs LY"
            st.metric(title, f"{fmt(cur)} {unit}", delta)
            if fiveavg is not None:
                st.caption(f"5-yr avg {fmt(fiveavg)} {unit}")

    def five(s, conv):
        vals = [conv(s[y]) for y in range(last - 5, last) if y in s.index]
        return sum(vals) / len(vals) if vals else None

    pconv = lambda v: cvt(v, crop, imperial)
    kpi(k1, "Production", pconv(prod[last]), pconv(prod[last - 1]) if last - 1 in prod.index else None,
        five(prod, pconv), lambda v: f"{v:,.1f}", ul)
    kpi(k2, "Area harvested", area[last] / 1000, area[last - 1] / 1000 if last - 1 in area.index else None,
        five(area, lambda v: v / 1000), lambda v: f"{v:,.2f}", "M ha")
    yconv = lambda v: yld(v, crop, imperial)
    kpi(k3, "Yield", yconv(yl[last]), yconv(yl[last - 1]) if last - 1 in yl.index else None,
        five(yl, yconv), lambda v: f"{v:,.2f}", yu)

    years = [y for y in prod.index if y >= last - 19]
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure(go.Bar(
            x=[labels[y] for y in years], y=[pconv(prod[y]) for y in years],
            marker_color=[C_LIME if bool(fc[y]) else C_GREEN for y in years],
            hovertemplate="%{x}: %{y:,.1f}<extra></extra>",
        ))
        fig.update_layout(**style.plot_layout(
            title=dict(text=f"Brazil {what} production — CONAB", font=dict(size=13)),
            yaxis_title=ul, height=320, showlegend=False))
        st.plotly_chart(fig, width="stretch")
    with c2:
        fig = go.Figure(go.Scatter(
            x=[labels[y] for y in years], y=[yconv(yl[y]) for y in years if y in yl.index],
            mode="lines+markers", line=dict(color=C_BLUE, width=2.5),
            hovertemplate="%{x}: %{y:,.2f}<extra></extra>",
        ))
        fig.update_layout(**style.plot_layout(
            title=dict(text=f"Brazil {what} yield — CONAB", font=dict(size=13)),
            yaxis_title=yu, height=320, showlegend=False))
        st.plotly_chart(fig, width="stretch")

    if crop == "Corn" and series_crop == "Corn":
        parts = {}
        for label_, name_ in (("1st crop", "Corn (1st crop)"), ("2nd crop (safrinha)", "Corn (2nd crop)"),
                              ("3rd crop", "Corn (3rd crop)")):
            d_ = db.conab_series(name_)
            t_ = d_[(d_.level == "total") & (d_.metric == "production_kt")].set_index("start_year")["value"]
            if not t_.empty:
                parts[label_] = t_
        if parts:
            style.sec("Corn production by crop")
            fig = go.Figure()
            for (label_, t_), color_ in zip(parts.items(), (C_GREEN, C_GOLD, C_BLUE)):
                fig.add_trace(go.Bar(x=[labels[y] for y in years],
                                     y=[pconv(float(t_.get(y, 0) or 0)) for y in years],
                                     name=label_, marker_color=color_))
            fig.update_layout(**style.plot_layout(
                title=dict(text="Brazil corn production by crop — CONAB", font=dict(size=13)),
                barmode="stack", yaxis_title=ul, height=340,
                legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
                margin=dict(l=10, r=10, t=75, b=45)))
            st.plotly_chart(fig, width="stretch")

    # States for the latest year
    st_df = df[(df.level == "state") & (df.start_year.isin([last, last - 1]))]
    pv = st_df.pivot_table(index="region", columns=["metric", "start_year"], values="value", aggfunc="first")
    if ("production_kt", last) in pv.columns:
        out = pd.DataFrame({
            "State": [f"{r} · {UF_NAMES.get(r, r)}" for r in pv.index],
            "prod": pv[("production_kt", last)].values,
            "area": pv.get(("area_kha", last), pd.Series(index=pv.index, dtype=float)).values,
            "yld": pv.get(("yield_kgha", last), pd.Series(index=pv.index, dtype=float)).values,
            "prev": (pv[("production_kt", last - 1)].values
                     if ("production_kt", last - 1) in pv.columns else [None] * len(pv)),
        })
        out = out[out["prod"] > 0].sort_values("prod", ascending=False).reset_index(drop=True)
        total = prod[last]
        c3, c4 = st.columns([1, 1.15])
        top = out.head(10)
        with c3:
            style.sec(f"Top states — {labels[last]}")
            fig = go.Figure(go.Bar(
                x=[pconv(v) for v in top["prod"]][::-1], y=top["State"].tolist()[::-1],
                orientation="h", marker_color=C_GREEN,
                hovertemplate="%{y}: %{x:,.1f}<extra></extra>"))
            fig.update_layout(**style.plot_layout(
                xaxis_title=ul, height=max(320, len(top) * 32), showlegend=False,
                margin=dict(l=170, r=8, t=12, b=40)))
            st.plotly_chart(fig, width="stretch")
        with c4:
            style.sec("State detail")
            tbl = pd.DataFrame({
                "State": out["State"],
                f"Production ({ul})": out["prod"].map(lambda v: f"{pconv(v):,.1f}"),
                "Share": (out["prod"] / total * 100).map(lambda v: f"{v:.1f}%"),
                "Area (M ha)": out["area"].map(lambda v: "—" if pd.isna(v) else f"{v / 1000:,.2f}"),
                f"Yield ({yu})": out["yld"].map(lambda v: "—" if pd.isna(v) else f"{yconv(v):,.2f}"),
                "Δ LY": [fmt_d(pconv(p - q)) if q is not None and not pd.isna(q) else "—"
                         for p, q in zip(out["prod"], out["prev"])],
            })
            st.dataframe(tbl.style.map(color_delta, subset=["Δ LY"]).set_properties(**{"font-size": "0.85rem"}),
                         width="stretch", hide_index=True, height=min(60 + len(tbl) * 36, 460))
    st.caption("Source: CONAB — Série Histórica das Safras (gov.br/conab), © Conab, CC BY-ND 3.0. Soybeans and "
               "corn by crop use JSA's CONAB workbooks for finished history (through 2024/25); 2025/26 onward and "
               "wheat come from CONAB's website, reloaded monthly.")
    if series_crop == crop:                       # typed-in estimates are for the total crop
        estimates_block("Brazil", {int(y): float(v) for y, v in prod.items()}, "CONAB")


# ── Agency estimates (typed in) vs an official/USDA baseline ──────────────────
EU27 = ["BE", "BG", "CZ", "DK", "DE", "EE", "IE", "EL", "ES", "FR", "HR", "IT", "CY", "LV", "LT", "LU",
        "HU", "MT", "NL", "AT", "PL", "PT", "RO", "SI", "SK", "FI", "SE"]

AGENCIES = {
    "Argentina": ["BAGE", "BCR", "Soybean & Corn Advisor", "Other"],
    "Ukraine":   ["UGA", "Ministry of Economy", "UkrAgroConsult", "APK-Inform", "Other"],
    "Russia":    ["SovEcon", "IKAR", "Ministry of Agriculture", "Rosstat", "Other"],
    "Australia": ["ABARES", "Other"],
    "EU":        ["European Commission", "EC MARS (JRC)", "Strategie Grains", "COCERAL", "Argus", "Other"],
    "France":    ["Agreste (Ministry)", "FranceAgriMer", "Strategie Grains", "Argus", "Other"],
    "Canada":    ["AAFC", "Other"],
    "Mexico":    ["SIAP (Agriculture ministry)", "Other"],
    "Brazil":    ["Soybean & Corn Advisor", "Other"],
    "United States": ["Soybean & Corn Advisor", "Other"],
}
for _agencies in AGENCIES.values():           # USDA FAS attaché (GAIN) and Expana apply to every origin
    _agencies[-1:-1] = ["USDA Attaché (GAIN)", "Expana"]
AGENCY_HINTS = {
    "Argentina": "BAGE = Bolsa de Cereales de Buenos Aires (weekly Panorama Agrícola + monthly estimates); "
                 "BCR = Bolsa de Comercio de Rosario (monthly report).",
    "Ukraine":   "UGA = Ukrainian Grain Association; the ministry, UkrAgroConsult and APK-Inform publish "
                 "forecasts through press releases.",
    "Russia":    "SovEcon and IKAR publish analyst forecasts; the ministry and Rosstat publish official figures.",
    "Australia": "ABARES Australian Crop Report is quarterly (Mar / Jun / Sep / Dec); next edition 1 December 2026.",
    "EU":        "European Commission balance sheets and the JRC MARS crop-monitoring bulletin; Strategie Grains, "
                 "COCERAL and Argus publish private forecasts.",
    "France":    "Agreste (the agriculture ministry's statistics service) and FranceAgriMer publish the official "
                 "French crop estimates; Strategie Grains and Argus publish private forecasts.",
    "Mexico":    "SIAP (Servicio de Información Agroalimentaria y Pesquera) is the official source, but its data "
                 "host is unreachable from outside Mexico, so its Cierre agrícola / Avance de siembras figures are typed in.",
    "Brazil":    "CONAB is loaded automatically above; the Soybean & Corn Advisor's weekly report is loaded by "
                 "etl/load_advisor_report.py; add any other Brazilian estimate here.",
    "United States": "USDA (WASDE / Crop Production) is the baseline; the Soybean & Corn Advisor's weekly report "
                 "carries its own U.S. corn and soybean estimates.",
    "Canada":    "Statistics Canada is loaded automatically; AAFC (Agriculture and Agri-Food Canada) publishes a "
                 "monthly outlook with its own production forecasts.",
}
MARKER_COLORS = [C_ORANGE, C_BLUE, C_GOLD, C_RED, "#9b89c4", "#2f7d7a"]


def _campaign_year(c: str) -> int:
    return int(str(c)[:4])


def _short_campaign(c: str) -> str:
    """'2024/2025' -> '2024/25'"""
    a, _, b = str(c).partition("/")
    return f"{a}/{b[-2:]}" if b else a


def estimates_block(country: str, baseline: dict, baseline_name: str, extra: dict | None = None):
    """KPIs, comparison chart, table and entry form for one country's typed-in agency estimates.

    baseline: {start_year: production in 1,000 MT} from the official/USDA series.
    extra: optional {name: {start_year: 1,000 MT}} drawn as open-circle reference markers (e.g. USDA PSD).
    """
    ag = db.agency_estimates(country)
    ag = ag[ag.crop == crop] if not ag.empty else ag
    names = sorted(ag.agency.unique()) if not ag.empty else []
    color = {n: MARKER_COLORS[i % len(MARKER_COLORS)] for i, n in enumerate(names)}

    style.sec(f"{country} — agency estimates vs {baseline_name}")

    if ag.empty:
        st.info(f"No agency estimates entered yet for {country} {crop.lower()}. "
                f"{AGENCY_HINTS[country]} Add one below.")
    else:
        latest = ag.groupby("agency", sort=False).head(1)            # newest report per agency
        cols = st.columns(min(len(latest), 4))
        for col, r in zip(cols, latest.itertuples()):
            with col:
                st.metric(f"{r.agency} · {r.campaign}",
                          f"{cvt(r.production_mmt * 1000, crop, imperial):,.1f} {ul}"
                          if pd.notna(r.production_mmt) else "N/A",
                          help=f"Report dated {r.report_date}"
                               + (f". Planted area {r.planted_mha:,.2f} M ha." if pd.notna(r.planted_mha) else ""))
                st.caption(f"{r.report_date}")

    pts = {}                                    # agency -> {year: kt} (newest report wins)
    for r in ag.itertuples():
        if pd.notna(r.production_mmt):
            pts.setdefault(r.agency, {}).setdefault(_campaign_year(r.campaign), r.production_mmt * 1000)
    years = sorted(set(baseline) | {y for d in pts.values() for y in d})[-12:]
    if years and (baseline or pts):
        xl = [f"{y}/{str(y + 1)[-2:]}" for y in years]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=xl, y=[cvt(baseline[y], crop, imperial) if y in baseline else None for y in years],
                             name=baseline_name, marker_color=C_GREEN,
                             hovertemplate="%{x}: %{y:,.1f}<extra>" + baseline_name + "</extra>"))
        for n, d in pts.items():
            fig.add_trace(go.Scatter(
                x=xl, y=[cvt(d[y], crop, imperial) if y in d else None for y in years], name=n, mode="markers",
                marker=dict(symbol="diamond", size=13, color=color[n], line=dict(width=1, color="white")),
                hovertemplate="%{x}: %{y:,.1f}<extra>" + n + "</extra>"))
        for n, d in (extra or {}).items():
            fig.add_trace(go.Scatter(
                x=xl, y=[cvt(d[y], crop, imperial) if y in d else None for y in years], name=n, mode="markers",
                marker=dict(symbol="circle-open", size=12, color=style.TEXT, line=dict(width=2)),
                hovertemplate="%{x}: %{y:,.1f}<extra>" + n + "</extra>"))
        fig.update_layout(**style.plot_layout(
            title=dict(text=f"{country} {crop.lower()} production — {baseline_name} vs agencies",
                       font=dict(size=13)),
            yaxis_title=ul, height=340))
        st.plotly_chart(fig, width="stretch")

    if not ag.empty:
        show = ag.copy()
        show["report_date"] = show["report_date"].astype(str)
        show = show.rename(columns={"report_date": "Report date", "agency": "Agency", "campaign": "Campaign",
                                    "planted_mha": "Planted (M ha)", "production_mmt": "Production (MMT)",
                                    "yield_tha": "Yield (t/ha)", "note": "Note"})
        st.dataframe(show[["id", "Report date", "Agency", "Campaign", "Planted (M ha)", "Production (MMT)",
                           "Yield (t/ha)", "Note"]],
                     width="stretch", hide_index=True, height=min(60 + len(show) * 36, 300))

    entry_form(country, ag)
    kw = {"EU": "European Union"}.get(country, country)
    st.caption(AGENCY_HINTS[country] + " Figures are typed in from the published reports. "
               f"[USDA FAS attaché (GAIN) reports for {kw}](https://www.fas.usda.gov/data/search?keyword={quote(kw)}) "
               "open FAS's report search; FAS blocks automated access, so attaché figures are typed in too.")


def entry_form(country: str, ag: pd.DataFrame):
    pw = _secret("EDIT_PASSWORD")
    if not pw:
        st.caption("Editing is locked: no `EDIT_PASSWORD` is set for this app.")
        return
    if not st.session_state.get("agency_ok"):
        with st.expander("Add or remove agency estimates"):
            entered = st.text_input("Edit password", type="password", key=f"pw_{country}")
            if entered and hmac.compare_digest(entered, pw):
                st.session_state["agency_ok"] = True
                st.rerun(scope="fragment")
            elif entered:
                st.error("Wrong password.")
        return

    with st.expander("Add or remove agency estimates", expanded=True):
        yrs = list(range(date.today().year + 1, date.today().year - 4, -1))
        with st.form(f"agency_form_{country}", clear_on_submit=True):
            f1, f2, f3 = st.columns(3)
            with f1:
                agency = st.selectbox("Agency", AGENCIES[country])
                other = st.text_input("If Other: agency name")
            with f2:
                camp = st.selectbox("Campaign / marketing year", [f"{y}/{str(y + 1)[-2:]}" for y in yrs],
                                    index=1 if len(yrs) > 1 else 0)
                rdate = st.date_input("Report date", value=date.today())
            with f3:
                prod_mmt = st.number_input(f"{crop} production (MMT)", min_value=0.0, step=0.1, format="%.2f")
                planted = st.number_input("Planted area (M ha)", min_value=0.0, step=0.1, format="%.2f")
            note = st.text_input("Note (e.g. report name, link)")
            if st.form_submit_button("Save estimate"):
                name = (other.strip() if agency == "Other" else agency)
                if not name:
                    st.error("Enter the agency name.")
                elif prod_mmt <= 0 and planted <= 0:
                    st.error("Enter at least a production or an area figure.")
                else:
                    y_tha = prod_mmt / planted if planted > 0 and prod_mmt > 0 else None
                    db.agency_add(country, name, crop, camp, rdate, planted or None, prod_mmt or None, y_tha, note)
                    st.success(f"Saved {name} {crop} {camp}.")
                    st.rerun(scope="fragment")
        if not ag.empty:
            lab = ag.set_index("id")
            d1, d2 = st.columns([3, 1])
            with d1:
                rid = st.selectbox("Remove an entry", ag["id"].tolist(), key=f"del_{country}",
                                   format_func=lambda i: f"#{i} · {lab.loc[i, 'agency']} · "
                                                         f"{lab.loc[i, 'campaign']} · {lab.loc[i, 'report_date']}")
            with d2:
                st.write("")
                if st.button("Delete", key=f"delbtn_{country}"):
                    db.agency_delete(rid)
                    st.rerun(scope="fragment")
        if st.button("Lock editing", key=f"lock_{country}"):
            st.session_state["agency_ok"] = False
            st.rerun(scope="fragment")


# ── Argentina · MAGyP + BAGE + BCR ────────────────────────────────────────────
@st.fragment
def argentina_tab():
    if crop not in _CORE:
        st.info("MAGyP history is loaded for soybeans, corn and wheat only. Agency estimates can still be "
                "entered for other crops via the tabs for Ukraine, Russia and Australia.")
        return
    mg = db.magyp_series(crop)
    baseline = {}
    nat = pd.DataFrame()
    if not mg.empty:
        nat = (mg.groupby(["start_year", "campania"], as_index=False)
                 [["planted_ha", "harvested_ha", "production_t"]].sum(min_count=1)
                 .sort_values("start_year"))
        baseline = {int(r.start_year): r.production_t / 1000 for r in nat.itertuples() if pd.notna(r.production_t)}
    estimates_block("Argentina", baseline, "MAGyP (official)")

    if not mg.empty:
        last = int(nat.start_year.max())
        cur = mg[mg.start_year == last].set_index("provincia")
        prv = mg[mg.start_year == last - 1].set_index("provincia")
        tot = cur.production_t.sum()
        cur = cur[cur.production_t > 0].sort_values("production_t", ascending=False)
        style.sec(f"Provinces — MAGyP {_short_campaign(nat[nat.start_year == last].campania.iloc[0])}")
        tbl = pd.DataFrame({
            "Province": cur.index,
            f"Production ({ul})": [f"{cvt(v / 1000, crop, imperial):,.1f}" for v in cur.production_t],
            "Share": [f"{v / tot * 100:.1f}%" for v in cur.production_t],
            "Planted (M ha)": [f"{v / 1e6:,.2f}" if pd.notna(v) else "—" for v in cur.planted_ha],
            f"Yield ({yu})": [f"{yld(p / h * 1000, crop, imperial):,.2f}" if pd.notna(h) and h else "—"
                              for p, h in zip(cur.production_t, cur.harvested_ha)],
            "Δ LY": [fmt_d(cvt((v - prv.production_t.get(p)) / 1000, crop, imperial))
                     if p in prv.index and pd.notna(prv.production_t.get(p)) else "—"
                     for p, v in zip(cur.index, cur.production_t)],
        })
        st.dataframe(tbl.style.map(color_delta, subset=["Δ LY"]).set_properties(**{"font-size": "0.85rem"}),
                     width="stretch", hide_index=True, height=min(60 + len(tbl) * 36, 420))
        st.caption("Official history: Argentina's agriculture ministry (MAGyP), CC BY 4.0. The latest campaign "
                   "in that file lags the current season.")


# ── Ukraine / Russia / Australia: USDA baseline + agencies ────────────────────
def usda_country_tab(country: str):
    ser = psd.country_series(crop, country)
    if ser.empty:
        st.warning(f"USDA PSD returned no {crop.lower()} data for {country} (or the feed is unreachable).")
        baseline = {}
    else:
        pv = ser.pivot_table(index="start_year", columns="metric", values="value", aggfunc="first").sort_index()
        baseline = pv["production_kt"].dropna().to_dict() if "production_kt" in pv else {}
        last = int(pv.index.max())
        style.sec(f"{country} — USDA PSD {last}/{str(last + 1)[-2:]}")
        k = st.columns(4)

        def kpi(col, title, metric, conv, fmt, unit):
            with col:
                if metric not in pv or last not in pv.index or pd.isna(pv.at[last, metric]):
                    st.metric(title, "N/A")
                    return
                cur = conv(pv.at[last, metric])
                prev = conv(pv.at[last - 1, metric]) if (last - 1) in pv.index and pd.notna(pv.at[last - 1, metric]) else None
                st.metric(title, f"{fmt(cur)} {unit}".strip(), f"{cur - prev:+,.2f} vs LY" if prev is not None else None)

        kpi(k[0], "Production", "production_kt", lambda v: cvt(v, crop, imperial), lambda v: f"{v:,.1f}", ul)
        kpi(k[1], "Area harvested", "area_kha", lambda v: v / 1000, lambda v: f"{v:,.2f}", "M ha")
        kpi(k[2], "Yield", "yield_tha", lambda v: yld(v * 1000, crop, imperial), lambda v: f"{v:,.2f}", yu)
        kpi(k[3], "Exports", "exports_kt", lambda v: cvt(v, crop, imperial), lambda v: f"{v:,.1f}", ul)
    estimates_block(country, baseline, "USDA PSD")
    st.caption(f"USDA FAS PSD, {country}, last five marketing years; refreshed hourly.")


@st.fragment
def mexico_tab():
    usda_country_tab("Mexico")


@st.fragment
def ukraine_tab():
    usda_country_tab("Ukraine")


@st.fragment
def russia_tab():
    usda_country_tab("Russia")


@st.fragment
def australia_tab():
    usda_country_tab("Australia")


# ── Official national series with KPIs (Canada, EU, France) ───────────────────
def national_kpis(prod_kt: dict, area_kha: dict, yield_kgha: dict, title: str):
    """Production / area / yield KPIs for the latest year in prod_kt, vs last year and the prior 5-yr average."""
    if not prod_kt:
        return
    last = max(prod_kt)
    style.sec(title.format(year=f"{last}/{str(last + 1)[-2:]}"))
    k = st.columns(3)

    def card(col, name, series, conv, fmt, unit):
        with col:
            if last not in series or pd.isna(series[last]):
                st.metric(name, "N/A")
                return
            cur = conv(series[last])
            prev = conv(series[last - 1]) if (last - 1) in series and pd.notna(series[last - 1]) else None
            st.metric(name, f"{fmt(cur)} {unit}", f"{cur - prev:+,.2f} vs LY" if prev is not None else None)
            avg = [conv(series[y]) for y in range(last - 5, last) if y in series and pd.notna(series[y])]
            if avg:
                st.caption(f"5-yr avg {fmt(sum(avg) / len(avg))} {unit}")

    card(k[0], "Production", prod_kt, lambda v: cvt(v, crop, imperial), lambda v: f"{v:,.1f}", ul)
    card(k[1], "Area", area_kha, lambda v: v / 1000, lambda v: f"{v:,.2f}", "M ha")
    card(k[2], "Yield", yield_kgha, lambda v: yld(v, crop, imperial), lambda v: f"{v:,.2f}", yu)


def _usda_production(country: str) -> dict:
    ser = psd.country_series(crop, country)
    if ser.empty:
        return {}
    pv = ser[ser.metric == "production_kt"].set_index("start_year")["value"]
    return pv.to_dict()


@st.fragment
def canada_tab():
    sc = db.statcan_series(crop)
    if sc.empty:
        st.info("No Statistics Canada data loaded yet. Run `python etl/fetch_south_america.py --only statcan`.")
        return
    nat = sc[sc.geo == "Canada"].sort_values("year")
    prod = {int(r.year): r.production_t / 1000 for r in nat.itertuples() if pd.notna(r.production_t)}
    area = {int(r.year): r.harvested_ha / 1000 for r in nat.itertuples() if pd.notna(r.harvested_ha)}
    yl = {int(r.year): r.yield_kgha for r in nat.itertuples() if pd.notna(r.yield_kgha)}
    national_kpis(prod, area, yl, "Canada — Statistics Canada {year}")
    estimates_block("Canada", prod, "Statistics Canada", {"USDA PSD": _usda_production("Canada")})

    last = max(prod)
    agg = {"Canada", "East", "West", "Prairie provinces", "Maritime provinces"}
    cur = sc[(sc.year == last) & ~sc.geo.isin(agg)].set_index("geo")
    prv = sc[(sc.year == last - 1) & ~sc.geo.isin(agg)].set_index("geo")
    cur = cur[cur.production_t > 0].sort_values("production_t", ascending=False)
    if not cur.empty:
        tot = prod[last] * 1000
        style.sec(f"Provinces — {last}/{str(last + 1)[-2:]}")
        tbl = pd.DataFrame({
            "Province": cur.index,
            f"Production ({ul})": [f"{cvt(v / 1000, crop, imperial):,.1f}" for v in cur.production_t],
            "Share": [f"{v / tot * 100:.1f}%" for v in cur.production_t],
            "Harvested (M ha)": [f"{v / 1e6:,.2f}" if pd.notna(v) else "—" for v in cur.harvested_ha],
            f"Yield ({yu})": [f"{yld(v, crop, imperial):,.2f}" if pd.notna(v) else "—" for v in cur.yield_kgha],
            "Δ LY": [fmt_d(cvt((v - prv.production_t.get(g)) / 1000, crop, imperial))
                     if g in prv.index and pd.notna(prv.production_t.get(g)) else "—"
                     for g, v in zip(cur.index, cur.production_t)],
        })
        st.dataframe(tbl.style.map(color_delta, subset=["Δ LY"]).set_properties(**{"font-size": "0.85rem"}),
                     width="stretch", hide_index=True, height=min(60 + len(tbl) * 36, 420))
    st.caption("Source: Statistics Canada, Table 32-10-0359-01, under the Statistics Canada Open Licence. "
               "The current year is a model-based estimate until the final survey release.")


def eurostat_view(country: str, geo: str, members: bool):
    es = db.eurostat_series(crop)
    if es.empty:
        st.info("No Eurostat data loaded yet. Run `python etl/fetch_south_america.py --only eurostat`.")
        return
    nat = es[es.geo == geo].sort_values("year")
    prod = {int(r.year): r.production_kt for r in nat.itertuples() if pd.notna(r.production_kt)}
    area = {int(r.year): r.area_kha for r in nat.itertuples() if pd.notna(r.area_kha)}
    yl = {int(r.year): r.yield_tha * 1000 for r in nat.itertuples() if pd.notna(r.yield_tha)}
    for y in prod:      # Eurostat leaves yield blank for some aggregates: derive it (kt / kha = t/ha)
        if y not in yl and area.get(y):
            yl[y] = prod[y] / area[y] * 1000
    national_kpis(prod, area, yl, f"{country} — Eurostat {{year}}")
    extra = {"USDA PSD": _usda_production("EU")} if country == "EU" else None
    estimates_block(country, prod, "Eurostat", extra)

    if members and prod:
        last = max(prod)
        cur = es[(es.year == last) & es.geo.isin(EU27)].set_index("geo_name")
        prv = es[(es.year == last - 1) & es.geo.isin(EU27)].set_index("geo_name")
        cur = cur[cur.production_kt > 0].sort_values("production_kt", ascending=False).head(15)
        if not cur.empty:
            style.sec(f"Member states — {last}/{str(last + 1)[-2:]}")
            tot = prod[last]
            tbl = pd.DataFrame({
                "Country": cur.index,
                f"Production ({ul})": [f"{cvt(v, crop, imperial):,.1f}" for v in cur.production_kt],
                "Share": [f"{v / tot * 100:.1f}%" for v in cur.production_kt],
                "Area (M ha)": [f"{v / 1000:,.2f}" if pd.notna(v) else "—" for v in cur.area_kha],
                f"Yield ({yu})": [f"{yld(v * 1000, crop, imperial):,.2f}" if pd.notna(v) else "—"
                                  for v in cur.yield_tha],
                "Δ LY": [fmt_d(cvt(v - prv.production_kt.get(g), crop, imperial))
                         if g in prv.index and pd.notna(prv.production_kt.get(g)) else "—"
                         for g, v in zip(cur.index, cur.production_kt)],
            })
            st.dataframe(tbl.style.map(color_delta, subset=["Δ LY"]).set_properties(**{"font-size": "0.85rem"}),
                         width="stretch", hide_index=True, height=min(60 + len(tbl) * 36, 460))
    st.caption("Source: Eurostat, apro_cpsh1 (crop production in EU standard humidity), CC BY 4.0. "
               "The newest year can be an early estimate, and member-state coverage is partial until Eurostat completes it.")


@st.fragment
def eu_tab():
    eurostat_view("EU", "EU27_2020", members=True)


@st.fragment
def france_tab():
    eurostat_view("France", "FR", members=False)


TABLE_COLORS = {"border": style.BORDER, "text": style.TEXT, "muted": style.TEXT_MUTED, "green": C_GREEN,
                "dark": "#3f4f44", "good": "#1f7a3d", "bad": C_RED}
SD_PRODUCTS = {"Soybeans": ["Soybeans", "Soybean meal", "Soybean oil"], "Corn": ["Corn"], "Wheat": ["Wheat"]}
SD_NAMES = {"Soybeans": "Soybeans", "Soybean meal": "Soybean meal", "Soybean oil": "Soybean oil",
            "Corn": "Corn", "Wheat": "Wheat"}


@st.fragment
def conab_balance():
    """Brazil's official supply & demand balance (CONAB 'Quadro de Oferta e Demanda'), latest vs prior report."""
    prods = SD_PRODUCTS.get(crop)
    if not prods:
        return
    style.sec("CONAB supply & demand balance sheet")
    view = st.segmented_control("Show", ["Current", "Δ vs prior report"], default="Current", key="sd_view") or "Current"
    delta = view != "Current"
    shown = False
    for prod in prods:
        d = db.conab_sd(prod)
        if d.empty:
            continue
        shown = True
        grain = prod == crop                                   # grain converts to bushels; meal/oil stay in tonnes
        unit = ul if grain else "1,000 t"
        seasons = (d.drop_duplicates("season").sort_values("start_year", ascending=False)["season"].tolist())
        cur = seasons[0]
        items = d.sort_values("item_order").drop_duplicates("item")["item"].tolist()

        def val(item, season, rank):
            m = d[(d.item == item) & (d.season == season) & (d.rev_rank == rank)]
            return float(m.value.iat[0]) if len(m) else None

        def conv(v):
            return None if v is None else (cvt(v, crop, imperial) if grain else v)

        rows = []
        for it in items:
            vals = {}
            for s in seasons:
                a = val(it, s, 0)
                if delta:
                    b = val(it, s, 1)
                    vals[s] = None if a is None or b is None else conv(a) - conv(b)
                else:
                    vals[s] = conv(a)
            rows.append((it, "qty", vals))
        if "Ending Stocks" in items and "Total Use" in items:
            su = {}
            for s in seasons:
                e, u = val("Ending Stocks", s, 0), val("Total Use", s, 0)
                e1, u1 = val("Ending Stocks", s, 1), val("Total Use", s, 1)
                now = e / u * 100 if e is not None and u else None
                if delta:
                    su[s] = now - e1 / u1 * 100 if now is not None and e1 is not None and u1 else None
                else:
                    su[s] = now
            rows.append(("Stocks-to-Use", "pct", su))
        revs = d[d.rev_rank == 0].rev_label.replace("", pd.NA).dropna()
        prev = d[d.rev_rank == 1].rev_label.replace("", pd.NA).dropna()
        title = SD_NAMES.get(prod, prod)
        st.markdown(f"<div style='font-weight:600;margin:10px 0 4px;color:{style.TEXT}'>Brazil {title}</div>",
                    unsafe_allow_html=True)
        st.markdown(sd_table.render(rows, seasons, cur, unit, TABLE_COLORS, signed=delta), unsafe_allow_html=True)
        if delta:
            if len(revs) and len(prev):
                st.caption(f"Change from the {prev.iloc[0]} report to {revs.iloc[0]} (only seasons CONAB revised).")
            else:
                st.caption("CONAB's soybean complex sheet carries no prior-report column, so no revision is shown.")
    if not shown:
        st.info("No CONAB balance sheet loaded yet. Run `python etl/fetch_south_america.py --only conabsd`.")
        return
    src = db.conab_sd(prods[0])
    st.caption(f"Source: CONAB, Quadro de Oferta e Demanda de Grãos ({src.source.iloc[0] if len(src) else ''}); "
               "monthly, 1,000 t unless converted. Marketing years follow CONAB's convention (wheat by calendar year). "
               "CONAB content is CC BY-ND 3.0.")


PSD_CROPS = {"Canola": "Canola (Rapeseed)"}


@st.fragment
def psd_balance(country: str, ccode: str):
    """Full USDA PSD supply & demand table for one country (history from Snowflake)."""
    name = PSD_CROPS.get(crop, crop)
    code = pm.COMMODITIES.get(name)
    if not code or code not in db.psd_loaded_commodities():
        return
    df = db.psd_history(code, ccode)
    if df.empty:
        return
    style.sec(f"{country} — USDA PSD balance sheet")
    span = st.segmented_control("History", ["10 years", "20 years", "All"], default="10 years",
                                key=f"psdbs_{ccode}") or "10 years"
    system = "imperial" if imperial else "metric"
    N = bs.derive(bs.basics(df))
    latest = int(N.index.max())
    n = {"10 years": 10, "20 years": 20}.get(span, 70)
    years = [y for y in range(latest - n + 1, latest + 1) if y in N.index]
    keys = bs.visible_keys(N, years, name)
    D = bs.to_display(bs.make_view(N, "current"), name, system, keys)
    st.markdown(f"<div style='color:{style.TEXT_MUTED};font-size:0.85rem;margin:-4px 0 6px'>"
                f"{country} {name} &nbsp;·&nbsp; {system.title()} &nbsp;·&nbsp; USDA FAS PSD</div>",
                unsafe_allow_html=True)
    st.markdown(bs.render_html(D, years, keys, name, system, "current", latest, TABLE_COLORS),
                unsafe_allow_html=True)
    st.caption("USDA's own balance sheet for the country (the shaded column is the current marketing year); "
               "the Balance Sheet page has the same table for any country with the full history and CSV download.")


def _auto_source(country: str):
    """The automatic official/statistical series for a country: (name, {start_year: (prod_kt, area_kha)})."""
    try:
        if country == "Brazil" and crop in _CORE:
            df = db.conab_series(crop)
            t = df[df.level == "total"]
            p = t[t.metric == "production_kt"].set_index("start_year").value
            a = t[t.metric == "area_kha"].set_index("start_year").value
            return "CONAB", {int(y): (v, a.get(y)) for y, v in p.items()}
        if country == "Argentina" and crop in _CORE:
            mg = db.magyp_series(crop)
            g = mg.groupby("start_year")[["planted_ha", "production_t"]].sum(min_count=1)
            return "MAGyP (ministry)", {int(y): (r.production_t / 1000, r.planted_ha / 1000)
                                        for y, r in g.iterrows() if pd.notna(r.production_t)}
        if country == "Canada":
            sc = db.statcan_series(crop)
            n = sc[sc.geo == "Canada"]
            return "Statistics Canada", {int(r.year): (r.production_t / 1000,
                                                       r.planted_ha / 1000 if pd.notna(r.planted_ha) else None)
                                         for r in n.itertuples() if pd.notna(r.production_t)}
        if country == "EU":
            es = db.eurostat_series(crop)
            n = es[es.geo == "EU27_2020"]
            return "Eurostat", {int(r.year): (r.production_kt, r.area_kha)
                                for r in n.itertuples() if pd.notna(r.production_kt)}
    except Exception:
        pass
    return None, {}


@st.fragment
def compare_block(country: str, ccode: str):
    """USDA vs every other source for one marketing year: production, area, yield and the gap to USDA."""
    name = PSD_CROPS.get(crop, crop)
    code = pm.COMMODITIES.get(name)
    usda = {}
    if code and code in db.psd_loaded_commodities():
        h = db.psd_history(code, ccode)
        if not h.empty:
            def ser(aid):
                return h[h.attribute_id == aid].drop_duplicates("market_year").set_index("market_year").value
            prod, planted, harv = ser(28), ser(1), ser(4)
            for y, v in prod.items():
                area = planted.get(y) if pd.notna(planted.get(y)) else harv.get(y)
                usda[int(y)] = (float(v), float(area) if area is not None and pd.notna(area) else None)
    ag = db.agency_estimates(country)
    ag = ag[ag.crop == crop].copy() if not ag.empty else ag
    auto_name, auto = _auto_source(country)
    if not usda and ag.empty and not auto:
        return
    ag_years = sorted({_campaign_year(c) for c in ag.campaign}) if not ag.empty else []
    options = sorted(set(ag_years) | ({max(usda)} if usda else set()) | ({max(auto)} if auto else set()),
                     reverse=True)[:8]
    if not options:
        return
    style.sec(f"{country} — USDA vs other estimates")
    yr = st.selectbox("Marketing year", options, index=0, key=f"cmp_{ccode}_{crop}",
                      format_func=lambda y: f"{y}/{str(y + 1)[-2:]}")

    rows = []                                    # (source, kind, prod_kt, area_kha, report note)
    if yr in usda:
        rows.append(("USDA (PSD)", "usda", usda[yr][0], usda[yr][1], "latest WASDE"))
    if auto_name and yr in auto:
        rows.append((auto_name, "auto", auto[yr][0], auto[yr][1], "official series"))
    if not ag.empty:
        cur = ag[ag.campaign.map(_campaign_year) == yr].groupby("agency", sort=False).head(1)
        for r in cur.itertuples():
            if pd.notna(r.production_mmt):
                rows.append((r.agency, "agency", r.production_mmt * 1000,
                             r.planted_mha * 1000 if pd.notna(r.planted_mha) else None, f"{r.report_date}"))
    if not rows:
        st.info(f"No estimates for {yr}/{str(yr + 1)[-2:]} yet.")
        return
    base = usda.get(yr, (None, None))[0]

    def fmt_gap(v):
        return "—" if v is None else f"{v:+,.1f}"

    tbl = []
    for src, kind, p, a, note in rows:
        gap = None if base is None or kind == "usda" else p - base
        tbl.append({
            "Source": src,
            f"Production ({ul})": f"{cvt(p, crop, imperial):,.1f}",
            f"vs USDA ({ul})": fmt_gap(None if gap is None else cvt(gap, crop, imperial)),
            "vs USDA %": "—" if gap is None else f"{gap / base * 100:+.1f}%",
            "Area (M ha)": f"{a / 1000:,.2f}" if a else "—",
            f"Yield ({yu})": f"{yld(p / a * 1000, crop, imperial):,.2f}" if a else "—",
            "As of": note,
        })
    st.dataframe(pd.DataFrame(tbl).style.map(color_delta, subset=["vs USDA %"]).set_properties(
        **{"font-size": "0.85rem"}), width="stretch", hide_index=True, height=min(60 + len(tbl) * 36, 360))

    colors = {"usda": C_GREEN, "auto": C_BLUE, "agency": C_GOLD}
    fig = go.Figure(go.Bar(
        x=[r[0] for r in rows], y=[cvt(r[2], crop, imperial) for r in rows],
        marker_color=[colors[r[1]] for r in rows],
        text=[f"{cvt(r[2], crop, imperial):,.1f}" for r in rows], textposition="outside", cliponaxis=False,
        hovertemplate="%{x}: %{y:,.1f}<extra></extra>"))
    fig.update_layout(**style.plot_layout(
        title=dict(text=f"{country} {crop.lower()} {yr}/{str(yr + 1)[-2:]} production — USDA vs other estimates",
                   font=dict(size=13)), yaxis_title=ul, height=330, showlegend=False))
    st.plotly_chart(fig, width="stretch")
    st.caption("Green = USDA PSD, blue = official/statistical series, gold = typed-in or emailed agency estimates "
               "(newest report per agency). Area is planted where the source gives it, otherwise USDA's harvested area; "
               "marketing years follow each source's own convention.")


@st.fragment
def us_tab():
    usda_country_tab("United States")


tab_br, tab_ar, tab_us, tab_ua, tab_ru, tab_au, tab_eu, tab_fr, tab_ca, tab_mx = st.tabs(
    ["Brazil · CONAB", "Argentina · BAGE / BCR", "United States", "Ukraine", "Russia", "Australia",
     "EU · Eurostat", "France", "Canada · StatCan", "Mexico"])
with tab_br:
    brazil_tab()
    compare_block("Brazil", "BR")
    conab_balance()
    psd_balance("Brazil", "BR")
with tab_ar:
    argentina_tab()
    compare_block("Argentina", "AR")
    psd_balance("Argentina", "AR")
with tab_us:
    us_tab()
    compare_block("United States", "US")
    psd_balance("United States", "US")
with tab_ua:
    ukraine_tab()
    compare_block("Ukraine", "UP")
    psd_balance("Ukraine", "UP")
with tab_ru:
    russia_tab()
    compare_block("Russia", "RS")
    psd_balance("Russia", "RS")
with tab_au:
    australia_tab()
    compare_block("Australia", "AS")
    psd_balance("Australia", "AS")
with tab_eu:
    eu_tab()
    compare_block("EU", "E4")
    psd_balance("EU", "E4")
with tab_fr:
    france_tab()
with tab_ca:
    canada_tab()
    compare_block("Canada", "CA")
    psd_balance("Canada", "CA")
with tab_mx:
    mexico_tab()
    compare_block("Mexico", "MX")
    psd_balance("Mexico", "MX")

style.disclaimer()
