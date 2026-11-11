"""U.S. planted acres by crop (NASS national, 1980+) into JSA.WASDE.US_PROGRESSION for the Balance Sheet page.

NASS's API terms steer dashboards away from calling it live, so this reads the pull-and-cache layer the NASS ETL already
fills (JSA.NASS_CACHE.NASS_CACHE: national 'AREA PLANTED' queries cached for the Domestic Production dashboard) instead of
calling NASS. Rows are stored as item 'Planted Area', kind 'current', source 'nass', in million acres; the marketing year
is the crop year (NASS 2026 planted = the 2026/27 balance sheet). Needs read access to NASS_CACHE (local admin role, or
GRANT SELECT on it to the ETL role).

    python etl/load_nass_planted.py --dry-run
    python etl/load_nass_planted.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from fetch_wasde import _sf_connect  # noqa: E402
from fetch_us_progression import _load  # noqa: E402

# NASS commodity_desc -> commodity name used in US_PROGRESSION / by the Balance Sheet page
CROPS = {"CORN": "Corn", "SOYBEANS": "Soybeans", "WHEAT": "Wheat", "BARLEY": "Barley", "OATS": "Oats",
         "SORGHUM": "Sorghum", "RICE": "Rice", "COTTON": "Cotton", "CANOLA": "Canola", "SUNFLOWER": "Sunflowerseed",
         "PEANUTS": "Peanuts"}
MIN_YEAR = 1980


def _rows(data) -> list[dict]:
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        data = data.get("data", [])
    return data if isinstance(data, list) else []


def collect(conn) -> pd.DataFrame:
    cur = conn.cursor()
    cur.execute("SELECT PARAMS:commodity_desc::string, TO_VARCHAR(DATA), FETCHED_AT FROM JSA.NASS_CACHE.NASS_CACHE "
                "WHERE PARAMS:statisticcat_desc::string = 'AREA PLANTED' AND PARAMS:agg_level_desc::string = 'NATIONAL' "
                "AND ERROR IS NULL AND ROW_COUNT > 0")
    best: dict[tuple[str, int], tuple] = {}                 # (crop, year) -> (fetched_at, million acres); the newest pull wins
    for com, data, fetched in cur.fetchall():
        if com not in CROPS:
            continue
        for r in _rows(data):
            if (r.get("state_alpha") != "US" or r.get("freq_desc") != "ANNUAL" or r.get("reference_period_desc") != "YEAR"
                    or r.get("unit_desc") != "ACRES" or r.get("statisticcat_desc") != "AREA PLANTED"
                    or r.get("class_desc") not in ("ALL CLASSES", "", None)
                    or r.get("prodn_practice_desc") not in ("ALL PRODUCTION PRACTICES", "", None)
                    or r.get("util_practice_desc") not in ("ALL UTILIZATION PRACTICES", "", None)
                    or r.get("domain_desc") not in ("TOTAL", "", None)):
                continue
            if not str(r.get("short_desc", "")).endswith("ACRES PLANTED"):
                continue
            try:
                year, val = int(r["year"]), float(str(r["Value"]).replace(",", ""))
            except (KeyError, ValueError):
                continue                                    # '(D)' and other suppressed values
            if year < MIN_YEAR:
                continue
            key = (com, year)
            if key not in best or fetched > best[key][0]:
                best[key] = (fetched, val / 1e6)
    rows = [(CROPS[c], y, "current", None, "Planted Area", v, "nass") for (c, y), (_, v) in sorted(best.items())]
    return pd.DataFrame(rows, columns=["commodity", "market_year", "kind", "release_date", "item", "value", "source"])


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = _sf_connect()
    df = collect(conn)
    if df.empty:
        sys.exit("No national AREA PLANTED rows found in JSA.NASS_CACHE.NASS_CACHE")
    for com, g in df.groupby("commodity"):
        last = g.sort_values("market_year").iloc[-1]
        print(f"  {com:15s} {g.market_year.min()}-{g.market_year.max()}  {len(g):3d} years   latest {last.market_year}: "
              f"{last.value:.2f} M ac")
    if not args.dry_run:
        _load(conn, df, "DELETE FROM JSA.WASDE.US_PROGRESSION WHERE source = %s AND item = %s", ("nass", "Planted Area"))
        print(f"Loaded {len(df)} planted-acre values.")
    conn.close()


if __name__ == "__main__":
    main()
