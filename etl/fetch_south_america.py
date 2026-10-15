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
reports) are typed in on the Origins page and live in JSA.WASDE.AGENCY_ESTIMATES.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import unicodedata
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
}
CONAB_METRICS = ["area_kha", "yield_kgha", "production_kt"]   # sheet order in every workbook
CONAB_REGIONS = {"NORTE", "NORDESTE", "CENTRO-OESTE", "SUDESTE", "SUL", "NORTE/NORDESTE", "CENTRO-SUL"}

MAGYP_PACKAGE = "https://datos.gob.ar/api/3/action/package_show?id=estimaciones-agricolas"
MAGYP_CROPS = {"soja total": "Soybeans", "maiz": "Corn", "trigo total": "Wheat"}

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
        r = requests.get(url, headers=UA, timeout=90)
        r.raise_for_status()
        df = parse_conab(crop, r.content)
        print(f"  CONAB {crop}: {len(df)} rows, latest {df.start_year.max()}")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


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
    ap.add_argument("--only", choices=["conab", "magyp"],
                    help="load just one source (the Droplet can't reach datos.magyp.gob.ar, so it runs --only conab)")
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

    conab = magyp = None
    if args.only in (None, "conab"):
        print("Fetching CONAB…")
        conab = fetch_conab()
    if args.only in (None, "magyp"):
        print("Fetching MAGyP…")
        magyp = fetch_magyp()
    if args.dry_run:
        print("Dry run: nothing written.")
        return
    conn = _sf_connect()
    if conab is not None:
        _replace(conn, "CONAB_SERIES", conab,
                 ["crop", "metric", "region", "level", "start_year", "label", "is_forecast", "value"])
    if magyp is not None:
        _replace(conn, "MAGYP_SERIES", magyp,
                 ["crop", "campania", "start_year", "provincia", "planted_ha", "harvested_ha", "production_t"])
    conn.close()
    print(f"Done. CONAB {0 if conab is None else len(conab)} rows, MAGyP {0 if magyp is None else len(magyp)} rows.")


if __name__ == "__main__":
    main()
