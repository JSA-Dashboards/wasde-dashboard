"""
Full USDA PSD history -> Snowflake JSA.WASDE.PSD_HISTORY.

One API call per commodity-year (country/all) returns every country's balance sheet; World is the sum of
the countries for additive attributes (verified equal to USDA's own World row) with yield recomputed.
The API allows ~1,000 calls/hour, so the first full load takes ~1.5 h; it is resumable (skips years already
stored) and the monthly refresh only re-pulls the last few marketing years, which USDA still revises.

Usage:
    python etl/fetch_psd_history.py --setup                  # create tables + grants
    python etl/fetch_psd_history.py                          # all commodities, 1960 -> next year (resumes)
    python etl/fetch_psd_history.py --commodity Corn --min-year 2020
    python etl/fetch_psd_history.py --refresh-only           # just the last 3 years of every commodity (monthly cron)

Environment: FAS_API_KEY (USDA FAS open-data key, sent as the api_key query parameter) plus the Snowflake
variables used by fetch_wasde.py.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from fetch_wasde import _sf_connect  # noqa: E402
from psd_meta import COMMODITIES, KG_PER_BALE, WORLD  # noqa: E402

API = "https://api.fas.usda.gov/api/psd"
UA = {"User-Agent": "JPSI-WASDE-ETL/1.0"}

DDL = [
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.PSD_HISTORY (
        commodity_code VARCHAR, country_code VARCHAR, market_year INTEGER, attribute_id INTEGER,
        unit_id INTEGER, value FLOAT, as_of INTEGER, loaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS JSA.WASDE.PSD_COUNTRIES (
        country_code VARCHAR, country_name VARCHAR, region_code VARCHAR)""",
    "GRANT SELECT ON TABLE JSA.WASDE.PSD_HISTORY TO ROLE WASDE_ROLE",
    "GRANT SELECT ON TABLE JSA.WASDE.PSD_COUNTRIES TO ROLE WASDE_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.PSD_HISTORY TO ROLE WASDE_ETL_ROLE",
    "GRANT SELECT, INSERT, DELETE, TRUNCATE ON TABLE JSA.WASDE.PSD_COUNTRIES TO ROLE WASDE_ETL_ROLE",
]

# Units whose values cannot be summed across countries (rates, ratios, per-hectare figures)
NON_ADDITIVE_UNITS = {11, 23, 24, 26, 28, 29, 33, 34, 35}
YIELD_ID, AREA_HARVESTED_ID, PRODUCTION_ID = 184, 4, 28


class Api:
    def __init__(self, key: str):
        self.key = key
        self.calls = 0

    def get(self, path: str):
        for attempt in range(6):
            try:
                r = requests.get(f"{API}{path}", params={"api_key": self.key}, headers=UA, timeout=90)
            except requests.RequestException:
                time.sleep(5 * (attempt + 1))
                continue
            self.calls += 1
            remaining = int(r.headers.get("X-Ratelimit-Remaining", "999"))
            if r.status_code == 429 or remaining < 8:
                print(f"    rate limit low ({remaining} left, HTTP {r.status_code}); pausing 5 min", flush=True)
                time.sleep(300)
                if r.status_code == 429:
                    continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (404, 400):
                return []
            time.sleep(5 * (attempt + 1))
        raise RuntimeError(f"PSD API failed for {path}")


def world_rows(rows: list[dict]) -> list[tuple]:
    """Aggregate country rows into World; returns insert tuples."""
    code, year = rows[0]["commodityCode"], int(rows[0]["marketYear"])
    sums: dict[int, float] = defaultdict(float)
    units: dict[int, int] = {}
    as_of = Counter()
    for r in rows:
        if r["countryCode"] == WORLD:
            continue
        aid = r["attributeId"]
        units[aid] = r["unitId"]
        as_of[int(r["calendarYear"]) * 100 + int(r["month"])] += 1
        if r["unitId"] not in NON_ADDITIVE_UNITS:
            sums[aid] += r["value"] or 0.0
    stamp = max(as_of) if as_of else None
    out = [(code, WORLD, year, aid, units[aid], v, stamp) for aid, v in sums.items()]
    prod, area = sums.get(PRODUCTION_ID), sums.get(AREA_HARVESTED_ID)
    if prod and area:
        if units.get(PRODUCTION_ID) == 27:                       # cotton: bales -> kg/ha
            out.append((code, WORLD, year, YIELD_ID, 29, prod * KG_PER_BALE / area, stamp))
        else:
            out.append((code, WORLD, year, YIELD_ID, 26, prod / area, stamp))
    return out


def to_tuples(rows: list[dict]) -> list[tuple]:
    return [(r["commodityCode"], r["countryCode"], int(r["marketYear"]), r["attributeId"], r["unitId"],
             r["value"], int(r["calendarYear"]) * 100 + int(r["month"])) for r in rows]


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--commodity", help="one commodity by display name, e.g. Corn")
    ap.add_argument("--min-year", type=int, default=1960)
    ap.add_argument("--refresh-only", action="store_true", help="re-pull only the last --refresh years")
    ap.add_argument("--refresh", type=int, default=3, help="always re-pull this many recent marketing years")
    ap.add_argument("--workers", type=int, default=4, help="parallel API calls")
    ap.add_argument("--dry-run", action="store_true")
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

    key = os.environ.get("FAS_API_KEY", "").strip()
    if not key:
        sys.exit("FAS_API_KEY is not set")
    api = Api(key)

    this_year = date.today().year
    last_year = this_year + 1
    recent_from = this_year - args.refresh
    names = [args.commodity] if args.commodity else list(COMMODITIES)
    conn = None if args.dry_run else _sf_connect()

    if conn is not None:                                         # country names, refreshed every run
        cur = conn.cursor()
        countries = api.get("/countries")
        cur.execute("DELETE FROM JSA.WASDE.PSD_COUNTRIES")
        cur.executemany("INSERT INTO JSA.WASDE.PSD_COUNTRIES VALUES (%s, %s, %s)",
                        [(c["countryCode"], c["countryName"].strip(), c["regionCode"]) for c in countries]
                        + [(WORLD, "World", "R00")])
        conn.commit()

    for name in names:
        code = COMMODITIES[name]
        have: set[int] = set()
        if conn is not None:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT market_year FROM JSA.WASDE.PSD_HISTORY WHERE commodity_code = %s", (code,))
            have = {int(r[0]) for r in cur.fetchall()}
        if args.refresh_only:
            years = list(range(recent_from, last_year + 1))
        else:
            years = [y for y in range(args.min_year, last_year + 1) if y not in have or y >= recent_from]
        if not years:
            print(f"{name}: up to date")
            continue
        print(f"{name} ({code}): fetching {len(years)} marketing years…", flush=True)
        tuples: list[tuple] = []
        got: list[int] = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:       # each call takes ~10 s
            results = list(pool.map(lambda y: (y, api.get(f"/commodity/{code}/country/all/year/{y}")), years))
        for y, rows in results:
            if not rows:
                continue
            tuples += to_tuples(rows) + world_rows(rows)
            got.append(y)
        print(f"  {len(got)} years with data, {len(tuples)} rows ({api.calls} API calls so far)", flush=True)
        if conn is None or not got:
            continue
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM JSA.WASDE.PSD_HISTORY WHERE commodity_code = %s AND market_year BETWEEN %s AND %s",
                        (code, min(got), max(got)))
            cur.executemany("INSERT INTO JSA.WASDE.PSD_HISTORY (commodity_code, country_code, market_year, "
                            "attribute_id, unit_id, value, as_of) VALUES (%s, %s, %s, %s, %s, %s, %s)", tuples)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"  {name}: stored", flush=True)
    if conn is not None:
        conn.close()
    print(f"Done. {api.calls} API calls.")


if __name__ == "__main__":
    main()
