"""
Page 7 — South America crop estimates
Brazil: CONAB historical series (area / yield / production by state), loaded to Snowflake by
etl/fetch_south_america.py.
Argentina: official MAGyP campaign history + BAGE (Bolsa de Cereales de Buenos Aires) estimates,
which have no automated feed and are typed in below (password-gated, stored in BAGE_ESTIMATES).
"""

import hmac
import os
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
import style  # noqa: E402
from style import C_BLUE, C_GOLD, C_GREEN, C_LIME, C_ORANGE, C_RED  # noqa: E402
from units import convert, unit_label  # noqa: E402

st.set_page_config(
    page_title="JSA WASDE — South America",
    page_icon="🌎",
    layout="wide",
    initial_sidebar_state="auto",
)
style.inject()
style.nav_bar()

CROPS = ["Soybeans", "Corn", "Wheat"]
_MT_TO_BU = {"Corn": 39.368, "Soybeans": 36.744, "Wheat": 36.744}
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
    """1,000 MT -> display unit."""
    return convert(kt, crop, imperial)


def yld(kg_ha, crop, imperial):
    """kg/ha -> t/ha (metric) or bu/ac (imperial)."""
    if imperial:
        return kg_ha / 1000 * _MT_TO_BU[crop] * HA_PER_ACRE
    return kg_ha / 1000


def yld_unit(imperial):
    return "bu/ac" if imperial else "t/ha"


# ── Controls ──────────────────────────────────────────────────────────────────
st.markdown("<div class='ctrl-bar'>", unsafe_allow_html=True)
_c1, _c2 = st.columns([2, 1])
with _c1:
    crop = st.selectbox("Crop", CROPS)
with _c2:
    imperial = st.toggle("Million bu", value=False)
st.markdown("</div>", unsafe_allow_html=True)

ul = unit_label(crop, imperial)
yu = yld_unit(imperial)

st.markdown(
    f"<div style='font-family:\"EB Garamond\",Georgia,serif;font-size:1.35rem;"
    f"font-weight:600;color:{style.TEXT};margin-bottom:2px'>"
    f"{crop} — South America crop estimates</div>",
    unsafe_allow_html=True,
)
st.markdown(f"<hr style='margin:6px 0 14px;border-color:{style.BORDER}'>", unsafe_allow_html=True)

if not db.snowflake_available():
    st.info("**Snowflake not configured.** This page reads CONAB_SERIES / MAGYP_SERIES / BAGE_ESTIMATES.")
    style.disclaimer()
    st.stop()


# ── Brazil · CONAB ────────────────────────────────────────────────────────────
@st.fragment
def brazil_tab():
    df = db.conab_series(crop)
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
            title=dict(text=f"Brazil {crop.lower()} production — CONAB", font=dict(size=13)),
            yaxis_title=ul, height=320, showlegend=False))
        st.plotly_chart(fig, width="stretch")
    with c2:
        fig = go.Figure(go.Scatter(
            x=[labels[y] for y in years], y=[yconv(yl[y]) for y in years if y in yl.index],
            mode="lines+markers", line=dict(color=C_BLUE, width=2.5),
            hovertemplate="%{x}: %{y:,.2f}<extra></extra>",
        ))
        fig.update_layout(**style.plot_layout(
            title=dict(text=f"Brazil {crop.lower()} yield — CONAB", font=dict(size=13)),
            yaxis_title=yu, height=320, showlegend=False))
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
    st.caption("Source: CONAB — Série Histórica das Safras (gov.br/conab). © Conab, licensed CC BY-ND 3.0. "
               "Latest column is CONAB's current estimate, revised monthly.")


# ── Argentina · MAGyP + BAGE ──────────────────────────────────────────────────
def _short_campaign(c: str) -> str:
    """'2024/2025' -> '2024/25'"""
    a, _, b = str(c).partition("/")
    return f"{a}/{b[-2:]}" if b else a


@st.fragment
def argentina_tab():
    mg = db.magyp_series(crop)
    bg = db.bage_estimates()
    bg = bg[bg.crop == crop] if not bg.empty else bg

    nat = pd.DataFrame()
    if not mg.empty:
        nat = (mg.groupby(["start_year", "campania"], as_index=False)
                 [["planted_ha", "harvested_ha", "production_t"]].sum(min_count=1)
                 .sort_values("start_year"))

    style.sec("Argentina — BAGE estimates vs official MAGyP history")

    k1, k2, k3 = st.columns(3)
    if not bg.empty:
        latest = bg.iloc[0]
        with k1:
            st.metric(f"BAGE {latest.campaign} production",
                      f"{cvt(latest.production_mmt * 1000, crop, imperial):,.1f} {ul}"
                      if pd.notna(latest.production_mmt) else "N/A",
                      help=f"BAGE report dated {latest.report_date}")
        with k2:
            st.metric("BAGE planted area",
                      f"{latest.planted_mha:,.2f} M ha" if pd.notna(latest.planted_mha) else "N/A")
        with k3:
            y = latest.yield_tha
            if pd.isna(y) and pd.notna(latest.production_mmt) and pd.notna(latest.planted_mha) and latest.planted_mha:
                y = latest.production_mmt / latest.planted_mha
            st.metric("BAGE implied yield",
                      f"{yld(y * 1000, crop, imperial):,.2f} {yu}" if pd.notna(y) else "N/A")
        st.caption(f"Latest BAGE entry: {latest.campaign}, report dated {latest.report_date}.")
    else:
        st.info("No BAGE estimates entered yet for this crop. Add one below "
                "(BAGE's site blocks automated access, so figures are typed in from their reports).")

    # Chart: MAGyP bars + BAGE markers
    if not nat.empty or not bg.empty:
        official = {int(r.start_year): cvt(r.production_t / 1000, crop, imperial)
                    for r in nat.itertuples() if pd.notna(r.production_t)}
        bage_pts = {}
        for r in bg.itertuples():       # bg is newest-first: first hit per campaign wins
            if pd.isna(r.production_mmt):
                continue
            yr = int(str(r.campaign)[:4])
            bage_pts.setdefault(yr, cvt(r.production_mmt * 1000, crop, imperial))
        allyrs = sorted(set(official) | set(bage_pts))
        allyrs = allyrs[-22:]
        xl = [f"{y}/{str(y + 1)[-2:]}" for y in allyrs]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=xl, y=[official.get(y) for y in allyrs], name="MAGyP (official)",
                             marker_color=C_GREEN, hovertemplate="%{x}: %{y:,.1f}<extra>MAGyP</extra>"))
        if bage_pts:
            fig.add_trace(go.Scatter(x=xl, y=[bage_pts.get(y) for y in allyrs], name="BAGE estimate",
                                     mode="markers", marker=dict(symbol="diamond", size=12, color=C_ORANGE,
                                                                 line=dict(width=1, color="white")),
                                     hovertemplate="%{x}: %{y:,.1f}<extra>BAGE</extra>"))
        fig.update_layout(**style.plot_layout(
            title=dict(text=f"Argentina {crop.lower()} production", font=dict(size=13)),
            yaxis_title=ul, height=340))
        st.plotly_chart(fig, width="stretch")

    # Provinces, latest official campaign
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

    # BAGE entries + (password-gated) entry form
    style.sec("BAGE entries")
    if bg.empty:
        st.caption("Nothing entered for this crop yet.")
    else:
        show = bg.copy()
        show["report_date"] = show["report_date"].astype(str)
        show = show.rename(columns={"report_date": "Report date", "campaign": "Campaign",
                                    "planted_mha": "Planted (M ha)", "production_mmt": "Production (MMT)",
                                    "yield_tha": "Yield (t/ha)", "note": "Note"})
        st.dataframe(show[["id", "Report date", "Campaign", "Planted (M ha)", "Production (MMT)",
                           "Yield (t/ha)", "Note"]],
                     width="stretch", hide_index=True, height=min(60 + len(show) * 36, 300))

    pw = _secret("EDIT_PASSWORD")
    if not pw:
        st.caption("Editing is locked: no `EDIT_PASSWORD` is set for this app.")
        return
    if not st.session_state.get("bage_ok"):
        with st.expander("Add or remove BAGE estimates"):
            entered = st.text_input("Edit password", type="password", key="bage_pw")
            if entered and hmac.compare_digest(entered, pw):
                st.session_state["bage_ok"] = True
                st.rerun(scope="fragment")
            elif entered:
                st.error("Wrong password.")
        return

    with st.expander("Add or remove BAGE estimates", expanded=True):
        yrs = list(range(date.today().year + 1, date.today().year - 4, -1))
        with st.form("bage_form", clear_on_submit=True):
            f1, f2, f3 = st.columns(3)
            with f1:
                camp = st.selectbox("Campaign", [f"{y}/{str(y + 1)[-2:]}" for y in yrs],
                                    index=1 if len(yrs) > 1 else 0)
            with f2:
                rdate = st.date_input("Report date", value=date.today())
            with f3:
                prod_mmt = st.number_input("Production (MMT)", min_value=0.0, step=0.1, format="%.2f")
            g1, g2 = st.columns(2)
            with g1:
                planted = st.number_input("Planted area (M ha)", min_value=0.0, step=0.1, format="%.2f")
            with g2:
                note = st.text_input("Note (e.g. Panorama Agrícola Semanal, 2026-10-08)")
            if st.form_submit_button("Save BAGE estimate"):
                if prod_mmt <= 0 and planted <= 0:
                    st.error("Enter at least a production or an area figure.")
                else:
                    y_tha = prod_mmt / planted if planted > 0 and prod_mmt > 0 else None
                    db.bage_add(crop, camp, rdate, planted or None, prod_mmt or None, y_tha, note)
                    st.success(f"Saved BAGE {crop} {camp}.")
                    st.rerun(scope="fragment")
        if not bg.empty:
            d1, d2 = st.columns([3, 1])
            with d1:
                rid = st.selectbox("Remove an entry", bg["id"].tolist(),
                                   format_func=lambda i: f"#{i} · {bg.set_index('id').loc[i, 'campaign']} · "
                                                         f"{bg.set_index('id').loc[i, 'report_date']}")
            with d2:
                st.write("")
                if st.button("Delete"):
                    db.bage_delete(rid)
                    st.rerun(scope="fragment")
        if st.button("Lock editing"):
            st.session_state["bage_ok"] = False
            st.rerun(scope="fragment")

    st.caption("BAGE = Bolsa de Cereales de Buenos Aires. Figures are entered from their published reports. "
               "Official history: Argentina's agriculture ministry (MAGyP), CC BY 4.0 — latest campaign "
               "in that file lags the current season.")


tab_br, tab_ar = st.tabs(["Brazil · CONAB", "Argentina · BAGE / MAGyP"])
with tab_br:
    brazil_tab()
with tab_ar:
    argentina_tab()

style.disclaimer()
