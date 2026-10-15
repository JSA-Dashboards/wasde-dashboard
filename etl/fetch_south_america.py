"""
South America crop-estimate ETL: CONAB (Brazil) + MAGyP (Argentina) -> Snowflake.

Usage:
    python etl/fetch_south_america.py --setup      # create tables + grants (once, as an admin role)
    python etl/fetch_south_america.py              # reload CONAB + MAGyP
    python etl/fetch_south_america.py --dry-run    # parse only, print counts

CONAB: historical-series workbooks (area / yield / production by region and state).
  Site licence CC BY-ND 3.0 - show with attribution, don't republish adapted files.
MAGyP: official Argentine estimates CSV (CC BY 4.0), aggregated here to province level.
Agency estimates with no automated feed (BAGE and BCR for Argentina, UGA/ministry/UkrAgroConsult for
Ukraine, SovEcon/IKAR for Russia, ABARES for Australia - their sites are bot-protected or only publish
reports) are typed in on the Alternative Groups page and live in JSA.WASDE.AGENCY_ESTIMATES.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import time
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from fetch_wasde import _sf_connect  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (JPSI-WASDE-ETL)"}
CONAB_BASE = ("https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/safras/"
              "series-historicas/graos")
CONAB_FILES = {
    "Soybeans": f"{CONAB_BASE}/soja/sojaseriehist.xls",
    "Corn":     f"{CONAB_BASE}/milho/milhototalseriehist.xls",
    "Wheat":    f"{CONAB_BASE}/trigo/trigoseriehist.xls",
    # Brazil corn by crop: 1st (summer), 2nd (safrinha, the big export crop) and 3rd (northeast)
    "Corn (1st crop)": f"{CONAB_BASE}/milho/milho1aseriehist.xls",
    "Corn (2nd crop)": f"{CONAB_BASE}/milho/milho2aseriehist.xls",
    "Corn (3rd crop)": f"{CONAB_BASE}/milho/milho3aseriehist.xls",
}
CONAB_METRICS = ["area_kha", "yield_kgha", "production_kt"]   # sheet order in every workbook
CONAB_REGIONS = {"NORTE", "NORDESTE", "CENTRO-OESTE", "SUDESTE", "SUL", "NORTE/NORDESTE", "CENTRO-SUL"}

CONAB_SD_LANDING = ("https://www.gov.br/conab/pt-br/atuacao/informacoes-agropecuarias/"
                    "analises-do-mercado-agropecuario-e-extrativista/analises-de-mercado/"
                    "quadro-de-oferta-e-demanda/graos")
MONTHS_PT = {"janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3, "abril": 4, "abr": 4,
             "maio": 5, "mai": 5, "junho": 6, "jun": 6, "julho": 7, "jul": 7, "agosto": 8, "ago": 8,
             "setembro": 9, "set": 9, "outubro": 10, "out": 10, "novembro": 11, "nov": 11,
             "dezembro": 12, "dez": 12}
SD_PRODUCTS = {"algodao": "Cotton", "arroz em casca": "Rice (paddy)", "feijao": "Beans", "milho": "Corn",
               "trigo": "Wheat", "soja": "Soybeans", "farelo de soja": "Soybean meal", "oleo de soja": "Soybean oil",
               "sorgo": "Sorghum", "aveia": "Oats", "cevada": "Barley", "centeio": "Rye", "triticale": "Triticale"}
SD_COLS = {"estoqueinicial": "Beginning Stocks", "producao": "Production", "importacao": "Imports",
           "suprimento": "Total Supply", "consumo": "Domestic Use", "exportacao": "Exports",
           "demandatotal": "Total Use", "estoquefinal": "Ending Stocks"}
SD_SOY_ITEMS = {"estoque inicial": "Beginning Stocks", "producao": "Production", "importacao": "Imports",
                "sementes/outros": "Seed & Other", "exportacao": "Exports", "processamento": "Crush",
                "estoque final": "Ending Stocks", "vendas no mercado interno": "Domestic Sales"}
SD_SOY_PRODUCTS = {"soja em grao": "Soybeans", "farelo": "Soybean meal", "oleo": "Soybean oil"}

MAGYP_PACKAGE = "https://datos.gob.ar/api/3/action/package_show?id=estimaciones-agricolas"
MAGYP_CROPS = {"soja total": "Soybeans", "maiz": "Corn", "trigo total": "Wheat"}

STATCAN_WDS = "https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/32100359/en"
STATCAN_CROPS = {"Soybeans": "Soybeans", "Corn": "Corn for grain", "Wheat": "Wheat, all",
                 "Barley": "Barley", "Canola": "Canola (rapeseed)"}
STATCAN_METRICS = {"Seeded area (hectares)": "planted_ha", "Harvested area (hectares)": "harvested_ha",
                   "Production (metric tonnes)": "production_t", "Average yield (kilograms per hectare)": "yield_kgha"}

EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/apro_cpsh1"
EUROSTAT_CROPS = {"Wheat": "C1100", "Corn": "C1500", "Barley": "C1300", "Canola": "I1110", "Soybeans": "I1130"}
EUROSTAT_METRICS = {"AR_THS_HA": "area_kha", "HPRD_HUMD_EU_THS_T": "production_kt", "YLD_HUMD_EU_T_HA": "yield_tha"}

DDL = [
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.CONAB_SERIES (
        crop VARCHAR, metric VARCHAR, region VARCHAR, level VARCHAR,
        start_year INTEGER, label VARCHAR, is_forecast BOOLEAN, value FLOAT,
        loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.MAGYP_SERIES (
        crop VARCHAR, campania VARCHAR, start_year INTEGER, provincia VARCHAR,
        planted_ha FLOAT, harvested_ha FLOAT, production_t FLOAT,
        loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.AGENCY_ESTIMATES (
        id INTEGER AUTOINCREMENT, country VARCHAR NOT NULL, agency VARCHAR NOT NULL,
        crop VARCHAR NOT NULL, campaign VARCHAR NOT NULL,
        report_date DATE NOT NULL, planted_mha FLOAT, production_mmt FLOAT,
        yield_tha FLOAT, note VARCHAR,
        entered_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    "GRANT SELECT ON TABLE JSA.WASDE.CONAB_SERIES TO ROLE WASDE_ROLE",
    "GRANT SELECT ON TABLE JSA.WASDE.MAGYP_SERIES TO ROLE WASDE_ROLE",
    "GRANT SELECT, INSERT, DELETE ON TABLE JSA.WASDE.AGENCY_ESTIMATES TO ROLE WASDE_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.CONAB_SERIES TO ROLE WASDE_ETL_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.MAGYP_SERIES TO ROLE WASDE_ETL_ROLE",
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.STATCAN_SERIES (
        crop VARCHAR, geo VARCHAR, year INTEGER, planted_ha FLOAT, harvested_ha FLOAT,
        production_t FLOAT, yield_kgha FLOAT, loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.EUROSTAT_SERIES (
        crop VARCHAR, geo VARCHAR, geo_name VARCHAR, year INTEGER, area_kha FLOAT,
        production_kt FLOAT, yield_tha FLOAT, loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.CONAB_SD (
        product VARCHAR, item VARCHAR, item_order INTEGER, season VARCHAR, start_year INTEGER,
        rev_rank INTEGER, rev_label VARCHAR, value FLOAT, source VARCHAR,
        loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    "GRANT SELECT ON TABLE JSA.WASDE.CONAB_SD TO ROLE WASDE_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.CONAB_SD TO ROLE WASDE_ETL_ROLE",
    "GRANT SELECT ON TABLE JSA.WASDE.STATCAN_SERIES TO ROLE WASDE_ROLE",
    "GRANT SELECT ON TABLE JSA.WASDE.EUROSTAT_SERIES TO ROLE WASDE_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.STATCAN_SERIES TO ROLE WASDE_ETL_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.EUROSTAT_SERIES TO ROLE WASDE_ETL_ROLE",
]


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().strip().lower()


def parse_conab(crop: str, content: bytes) -> pd.DataFrame:
    book = pd.ExcelFile(io.BytesIO(content))
    rows = []
    for sheet_i, metric in enumerate(CONAB_METRICS):
        df = book.parse(book.sheet_names[sheet_i], header=None)
        header = df.iloc[5].tolist()
        for _, r in df.iloc[6:].iterrows():
            region = str(r.iloc[0]).strip()
            if region.lower().startswith("legenda") or region.lower() == "nan":
                break
            level = "total" if region == "BRASIL" else "region" if region in CONAB_REGIONS else "state"
            for ci in range(1, len(header)):
                lab = str(header[ci])
                m = re.match(r"\s*(\d{4})", lab)
                v = pd.to_numeric(r.iloc[ci], errors="coerce")
                if not m or pd.isna(v):
                    continue
                label = re.sub(r"\s+", " ", re.sub(r"\.0\b", "", lab)).strip()
                rows.append((crop, metric, region, level, int(m.group(1)),
                             label.split(" ")[0], "prev" in _norm(lab), float(v)))
    return pd.DataFrame(rows, columns=["crop", "metric", "region", "level",
                                       "start_year", "label", "is_forecast", "value"])


def fetch_conab() -> pd.DataFrame:
    frames = []
    for crop, url in CONAB_FILES.items():
        r = _get(url, 90, tries=6)                  # gov.br resets connections at random; retry with backoff
        time.sleep(2)
        df = parse_conab(crop, r.content)
        print(f"  CONAB {crop}: {len(df)} rows, latest {df.start_year.max()}")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def _month_of(url: str) -> int:
    """Month number in a CONAB file name (janeiro / jan / fev / setembro ...); 0 if none."""
    toks = re.findall(r"[a-z]+", _norm(url.rsplit("/", 1)[-1]))
    return max((MONTHS_PT[t] for t in toks if t in MONTHS_PT), default=0)


def latest_sd_url() -> str:
    """Newest monthly 'Quadro de Oferta e Demanda de Graos' workbook (landing -> newest year page -> newest month)."""
    land = _get(CONAB_SD_LANDING, 90, tries=6).text
    years = {}
    for h in re.findall(r'href="([^"]*quadro-de-oferta-e-demanda-de-graos[^"]*)"', land):
        if h.lower().split("?")[0].endswith((".xlsx", ".xls", ".ods")):
            continue
        m = re.search(r"(20\d\d)", h)
        if m:
            years.setdefault(int(m.group(1)), h)
    for y in sorted(years, reverse=True):
        time.sleep(2)
        page = _get(years[y], 90, tries=6).text
        files = {h for h in re.findall(r'href="([^"]*\.(?:xlsx|xls|ods))"', page, re.I)}
        files = [f for f in files if "oferta" in f.lower()]
        if files:
            return max(files, key=_month_of)
    raise RuntimeError("no CONAB supply/demand workbook found")


def _sd_book(content: bytes) -> pd.ExcelFile:
    # CONAB saves OpenDocument files with an .xlsx name; pick the reader from the file's own header
    engine = "odf" if b"opendocument" in content[:200].lower() else None
    return pd.ExcelFile(io.BytesIO(content), engine=engine)


def _season_key(v):
    t = str(v).strip()
    t = t[:-2] if t.endswith(".0") else t
    m = re.match(r"(\d{4})(/\d{2})?", t)
    return (m.group(0), int(m.group(1))) if m else (None, None)


def parse_sd(content: bytes, source: str) -> pd.DataFrame:
    """CONAB balance sheets: sheet 'Suprimento' (all crops, one row per season, monthly revisions as extra rows)
    and 'Suprimento - Soja' (soy / meal / oil detail with crush). Values in 1,000 t. rev_rank 0 = latest, 1 = prior."""
    book = _sd_book(content)
    rows = []

    main = next((n for n in book.sheet_names if _norm(n) == "suprimento"), None)
    if main:
        df = book.parse(main, header=None)
        hi = next(i for i in range(len(df)) if _norm(df.iat[i, 0]) == "produto")
        cols = {j: SD_COLS[_norm(df.iat[hi, j]).replace(" ", "")] for j in range(df.shape[1])
                if _norm(df.iat[hi, j]).replace(" ", "") in SD_COLS}
        order = {n: i for i, n in enumerate(SD_COLS.values())}
        prod = seas = None
        groups: dict = {}
        for i in range(hi + 1, len(df)):
            a = df.iat[i, 0]
            if isinstance(a, str) and _norm(a).startswith(("nota", "fonte", "legenda")):
                break
            if isinstance(a, str) and a.strip():
                prod = SD_PRODUCTS.get(_norm(a), a.strip().title())
            if pd.notna(df.iat[i, 1]):
                seas = df.iat[i, 1]
            if prod is None or seas is None:
                continue
            label, sy = _season_key(seas)
            vals = {n: pd.to_numeric(df.iat[i, j], errors="coerce") for j, n in cols.items()}
            if label is None or all(pd.isna(v) for v in vals.values()):
                continue
            rev = "" if pd.isna(df.iat[i, 2]) else str(df.iat[i, 2]).strip()
            groups.setdefault((prod, label, sy), []).append((rev, vals))
        for (p, label, sy), revs in groups.items():
            for rank, (rev, vals) in enumerate(reversed(revs[-2:])):
                for item, v in vals.items():
                    if pd.notna(v):
                        rows.append((p, item, order[item], label, sy, rank, rev, float(v), source))

    soy = next((n for n in book.sheet_names if "soja" in _norm(n)), None)
    if soy:
        df = book.parse(soy, header=None)
        hi = next(i for i in range(len(df)) if _norm(df.iat[i, 0]) == "produto")
        seasons = {j: _season_key(df.iat[hi + 1, j]) for j in range(1, df.shape[1])}
        seasons = {j: v for j, v in seasons.items() if v[0]}
        prod, n_item = None, 0
        for i in range(hi + 2, len(df)):
            a = df.iat[i, 0]
            if not isinstance(a, str):
                continue
            if _norm(a).startswith(("nota", "fonte", "estoque de passagem")):
                break
            m_prod = re.match(r"\s*\d+\.\s+(?!\d)(.+)", a)
            m_item = re.match(r"\s*\d+\.\d+\.\s+(.+)", a)
            if m_item and prod:
                key = _norm(m_item.group(1))
                item = SD_SOY_ITEMS.get(key) or next(
                    (v for k, v in SD_SOY_ITEMS.items() if key.startswith(k[:20])), m_item.group(1).strip())
                n_item += 1
                for j, (label, sy) in seasons.items():
                    v = pd.to_numeric(df.iat[i, j], errors="coerce")
                    if pd.notna(v):
                        rows.append((prod, item, 100 + n_item, label, sy, 0, "", float(v), source))
            elif m_prod:
                prod = SD_SOY_PRODUCTS.get(_norm(m_prod.group(1)), m_prod.group(1).strip())
                n_item = 0
    return pd.DataFrame(rows, columns=["product", "item", "item_order", "season", "start_year",
                                       "rev_rank", "rev_label", "value", "source"])


def fetch_conab_sd() -> pd.DataFrame:
    url = latest_sd_url()
    print(f"  CONAB S&D file: {url.rsplit('/', 1)[-1]}")
    time.sleep(2)
    df = parse_sd(_get(url, 120, tries=6).content, url.rsplit("/", 1)[-1])
    print(f"  CONAB S&D: {len(df)} rows, products {sorted(df['product'].unique())}")
    return df


def fetch_magyp() -> pd.DataFrame:
    pkg = requests.get(MAGYP_PACKAGE, headers=UA, timeout=60).json()["result"]
    res = next(x for x in pkg["resources"]
               if x.get("format") == "CSV" and "serie de tiempo" not in _norm(x.get("name", "")))
    print(f"  MAGyP file: {res['url'].rsplit('/', 1)[-1]}")
    raw = requests.get(res["url"], headers=UA, timeout=180).content
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    df["crop"] = df["cultivo"].map(lambda s: MAGYP_CROPS.get(_norm(s)))
    df = df[df.crop.notna()].copy()
    for c, n in [("superficie_sembrada_ha", "planted_ha"), ("superficie_cosechada_ha", "harvested_ha"),
                 ("produccion_tm", "production_t")]:
        df[n] = pd.to_numeric(df[c], errors="coerce")
    df["start_year"] = df["campania"].str[:4].astype(int)
    out = (df.groupby(["crop", "campania", "start_year", "provincia"], as_index=False)
             [["planted_ha", "harvested_ha", "production_t"]].sum(min_count=1))
    print(f"  MAGyP: {len(out)} province rows, campaigns {out.start_year.min()}-{out.start_year.max()}")
    return out


def _get(url: str, timeout: int = 180, tries: int = 3):
    """GET with retries (StatCan's file host occasionally stalls once)."""
    last = None
    for attempt in range(tries):
        if attempt:
            time.sleep(4 * attempt)
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
    raise last


def fetch_statcan() -> pd.DataFrame:
    """Statistics Canada table 32-10-0359-01 (Open Licence): area, yield, production by province."""
    url = _get(STATCAN_WDS, 60).json()["object"]
    z = zipfile.ZipFile(io.BytesIO(_get(url, 120).content))
    df = pd.read_csv(z.open("32100359.csv"), dtype=str, encoding="utf-8-sig",
                     usecols=["REF_DATE", "GEO", "Harvest disposition", "Type of crop", "VALUE"])
    inv = {v: k for k, v in STATCAN_CROPS.items()}
    df = df[df["Type of crop"].isin(inv) & df["Harvest disposition"].isin(STATCAN_METRICS)].copy()
    df["crop"] = df["Type of crop"].map(inv)
    df["metric"] = df["Harvest disposition"].map(STATCAN_METRICS)
    df["year"] = pd.to_numeric(df["REF_DATE"].str[:4], errors="coerce")
    df["VALUE"] = pd.to_numeric(df["VALUE"], errors="coerce")
    df = df[df.year >= 1960].dropna(subset=["year"])
    out = (df.pivot_table(index=["crop", "GEO", "year"], columns="metric", values="VALUE", aggfunc="first")
             .reset_index().rename(columns={"GEO": "geo"}))
    out["year"] = out["year"].astype(int)
    for c in STATCAN_METRICS.values():
        if c not in out:
            out[c] = None
    print(f"  StatCan: {len(out)} rows, years {out.year.min()}-{out.year.max()}")
    return out


def fetch_eurostat() -> pd.DataFrame:
    """Eurostat apro_cpsh1 (CC BY 4.0): crop production in EU standard humidity, EU27 + member states."""
    rows = []
    for crop, code in EUROSTAT_CROPS.items():
        q = "&".join(f"strucpro={m}" for m in EUROSTAT_METRICS)
        d = _get(f"{EUROSTAT}?format=JSON&lang=EN&crops={code}&{q}&sinceTimePeriod=2000", 120).json()
        dims, size = d["id"], d["size"]
        pos = {}
        for dim in dims:
            idx = d["dimension"][dim]["category"]["index"]
            pos[dim] = ([k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]
                        if isinstance(idx, dict) else list(idx))
        names = d["dimension"]["geo"]["category"].get("label", {})
        for flat, v in d["value"].items():
            rem, coord = int(flat), {}
            for dim, n in zip(reversed(dims), reversed(size)):
                rem, r = divmod(rem, n)
                coord[dim] = pos[dim][r]
            geo = coord["geo"]
            if geo == "EU27_2020" or len(geo) == 2:
                rows.append((crop, geo, names.get(geo, geo), int(coord["time"]),
                             EUROSTAT_METRICS[coord["strucpro"]], v))
    df = pd.DataFrame(rows, columns=["crop", "geo", "geo_name", "year", "metric", "value"])
    out = (df.pivot_table(index=["crop", "geo", "geo_name", "year"], columns="metric", values="value",
                          aggfunc="first").reset_index())
    for c in EUROSTAT_METRICS.values():
        if c not in out:
            out[c] = None
    print(f"  Eurostat: {len(out)} rows, years {out.year.min()}-{out.year.max()}")
    return out


def _replace(conn, table: str, df: pd.DataFrame, cols: list[str]):
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM JSA.WASDE.{table}")
        sql = f"INSERT INTO JSA.WASDE.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
                for r in df[cols].itertuples(index=False, name=None)]
        cur.executemany(sql, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="create tables + grants, then exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", action="append", choices=["conab", "conabsd", "magyp", "statcan", "eurostat"],
                    help="load just these sources (repeatable); the Droplet can't reach datos.magyp.gob.ar")
    args = ap.parse_args()

    if args.setup:
        conn = _sf_connect()
        cur = conn.cursor()
        for stmt in DDL:
            print(" ", stmt.split("(")[0].strip()[:80])
            cur.execute(stmt)
        conn.commit()
        conn.close()
        print("Setup done.")
        return

    want = set(args.only or ["conab", "conabsd", "magyp", "statcan", "eurostat"])
    jobs = {
        "conab":    ("CONAB", fetch_conab, "CONAB_SERIES",
                     ["crop", "metric", "region", "level", "start_year", "label", "is_forecast", "value"]),
        "conabsd":  ("CONAB S&D", fetch_conab_sd, "CONAB_SD",
                     ["product", "item", "item_order", "season", "start_year", "rev_rank", "rev_label",
                      "value", "source"]),
        "magyp":    ("MAGyP", fetch_magyp, "MAGYP_SERIES",
                     ["crop", "campania", "start_year", "provincia", "planted_ha", "harvested_ha", "production_t"]),
        "statcan":  ("StatCan", fetch_statcan, "STATCAN_SERIES",
                     ["crop", "geo", "year", "planted_ha", "harvested_ha", "production_t", "yield_kgha"]),
        "eurostat": ("Eurostat", fetch_eurostat, "EUROSTAT_SERIES",
                     ["crop", "geo", "geo_name", "year", "area_kha", "production_kt", "yield_tha"]),
    }
    data = {}
    for key, (label, fn, _, _) in jobs.items():
        if key in want:
            print(f"Fetching {label}…")
            data[key] = fn()
    if args.dry_run:
        print("Dry run: nothing written.")
        return
    conn = _sf_connect()
    for key, df in data.items():
        _replace(conn, jobs[key][2], df, jobs[key][3])
    conn.close()
    print("Done. " + ", ".join(f"{jobs[k][0]} {len(v)} rows" for k, v in data.items()))


if __name__ == "__main__":
    main()
