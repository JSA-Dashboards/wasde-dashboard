"""
WASDE ETL — pull USDA FAS PSD Composite Visualization API and store snapshots.

Usage:
    python etl/fetch_wasde.py --latest          # current reporting period only
    python etl/fetch_wasde.py --backfill        # all available periods (~24 months)
    python etl/fetch_wasde.py --dry-run --latest  # print rows, no DB write

The real API base is PSDOnlineApi (not psdonline/api/).
Each call to GetAllAttributeData returns a multi-year balance sheet table for
one (commodity, country, reporting period). We take column 0 (current period
estimate for the current crop year) plus all prior-year columns, storing each
as a snapshot row keyed by (release_year, release_month, market_year, commodity,
country_code, attribute_id).

Environment (load from .env or inject via systemd EnvironmentFile):
    SNOWFLAKE_ACCOUNT / USER / WAREHOUSE
    SNOWFLAKE_PRIVATE_KEY or SNOWFLAKE_PRIVATE_KEY_PATH [+ SNOWFLAKE_PRIVATE_KEY_PWD]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ────────────────────────────────────────────────────────────────────
API_BASE = "https://apps.fas.usda.gov/PSDOnlineApi/api/CompositeVisualization"

COMMODITIES = {
    "Corn":         "0440000",
    "Soybeans":     "2222000",
    "Wheat":        "0410000",
    "Cotton":       "2631000",
    "Rice, Milled": "0422110",
    "Soybean Oil":  "4232000",
}

# Country codes to capture per commodity
COUNTRIES = {
    "US":    "US",     # United States
    "World": "00",     # All Countries aggregate
}

# PSD attribute IDs we care about
WANTED_ATTR_IDS = {
    "4",   "7",   "20",  "28",  "40",
    "57",  "58",  "88",  "125", "154",
    "166", "176", "185", "203",
}
# Friendly name overrides (API names have trailing spaces)
ATTR_NAMES = {
    "4":   "Area Harvested",
    "7":   "Yield",
    "20":  "Beginning Stocks",
    "28":  "Production",
    "40":  "Imports",
    "57":  "Feed/Residual Use",
    "58":  "Food/Seed/Industrial Use",
    "88":  "Exports",
    "125": "Total Supply",
    "154": "Yield",
    "166": "Area Harvested",
    "176": "Ending Stocks",
    "185": "Total Use",
    "203": "Season Avg Price",
}

REGION_CODE = "R00"   # World region (covers all country queries)
ATTRIBUTE_ID_HINT = "4"  # any valid attribute; API ignores it for GetAllAttributeData


# ── HTTP session ──────────────────────────────────────────────────────────────
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "JPSI-WASDE-ETL/1.0"})
    retry = Retry(total=4, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ── Reporting period helpers ──────────────────────────────────────────────────
def get_reporting_periods(sess: requests.Session) -> list[str]:
    """Return all available reporting period codes, newest first (e.g. '202609')."""
    r = sess.get(f"{API_BASE}/GetReportingPeriods", timeout=30)
    r.raise_for_status()
    return [p["value"] for p in r.json()]


def parse_rp(rp: str) -> tuple[int, int]:
    """'202609' → (2026, 9)"""
    return int(rp[:4]), int(rp[4:])


# ── Balance-sheet fetch ────────────────────────────────────────────────────────
def fetch_balance_sheet(sess: requests.Session, commodity_code: str,
                        country_code: str, reporting_period: str) -> dict | None:
    payload = {
        "reportingPeriod":       reporting_period,
        "reportingCutoffPeriod": None,
        "commodityCode":         commodity_code,
        "regionCode":            REGION_CODE,
        "countryCode":           country_code,
        "attributeId":           ATTRIBUTE_ID_HINT,
    }
    r = sess.post(f"{API_BASE}/GetAllAttributeData",
                  json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def _parse_market_year(header: str) -> int | None:
    """'26/27 Sep\\'26' or '25/26 ' → 2025 (the starting year of the crop year)."""
    m = re.match(r"(\d{2})/\d{2}", header.strip())
    if not m:
        return None
    yy = int(m.group(1))
    return (2000 + yy) if yy >= 0 else None


# ── Row builder ────────────────────────────────────────────────────────────────
def build_rows(data: dict, commodity: str, country_label: str,
               country_code: str, release_year: int, release_month: int) -> list[tuple]:
    """
    Parse a GetAllAttributeData response into insert tuples.

    Column layout in data["attributeValues"][i]["attributeValues"]:
      idx 0  → current reporting period's estimate for the latest crop year  ✓ keep
      idx 1  → "Change" delta string                                         ✗ skip
      idx 2  → prior reporting period's estimate for the same crop year      ✗ skip (dup)
      idx 3+ → prior crop years' estimates                                    ✓ keep

    We parse the header at the matching position to determine market_year.
    """
    headers = data.get("headerValues", [])  # includes "Attribute" at idx 0
    # headers[1] = col 0 data, headers[2] = col 1 data ("Change"), ...
    # attributeValues[i]["attributeValues"] has len = len(headers)-1

    # Determine which column indices to keep and their market_years
    keep: list[tuple[int, int]] = []  # (col_idx_in_attr_vals, market_year)
    for col_idx, hdr in enumerate(headers[1:], start=0):
        h = hdr.strip()
        if not h or h.lower() == "change" or h.startswith("+") or h.startswith("-"):
            continue
        # Skip the "prior period for current crop year" column (always col_idx==2)
        if col_idx == 2:
            continue
        myr = _parse_market_year(h)
        if myr is None:
            continue
        keep.append((col_idx, myr))

    rows = []
    for attr in data.get("attributeValues", []):
        attr_id   = str(attr.get("attributeId", "")).strip()
        if attr_id not in WANTED_ATTR_IDS:
            continue
        attr_name = ATTR_NAMES.get(attr_id, attr.get("attributeName", "").strip())
        vals      = attr.get("attributeValues", [])

        for col_idx, myr in keep:
            if col_idx >= len(vals):
                continue
            raw = vals[col_idx]
            if raw is None or raw == "":
                continue
            # Strip any trailing delta text like "+123(+.5%)" — shouldn't appear, but guard
            raw_str = str(raw).split("(")[0].strip().replace(",", "")
            try:
                value = float(raw_str)
            except ValueError:
                continue

            rows.append((
                release_year,
                release_month,
                myr,
                commodity,
                country_code,
                country_label,
                attr_id,
                attr_name,
                value,
            ))

    return rows


# ── Snowflake helpers ─────────────────────────────────────────────────────────
def _load_private_key():
    path = (os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH") or "").strip()
    pem  = os.environ.get("SNOWFLAKE_PRIVATE_KEY") or ""
    if not path and not pem.strip():
        return None
    from cryptography.hazmat.primitives import serialization
    data = open(path, "rb").read() if path else pem.replace("\\n", "\n").encode()
    pwd  = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PWD") or None
    key  = serialization.load_pem_private_key(data, password=pwd.encode() if pwd else None)
    return key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _sf_connect():
    import snowflake.connector
    pk = _load_private_key()
    kw = dict(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER"],
        role      = os.environ.get("SNOWFLAKE_ROLE", "WASDE_ETL_ROLE"),
        warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE"),
        database  = "JSA",
        schema    = "WASDE",
        autocommit= False,
    )
    if pk:
        kw["private_key"] = pk
    else:
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kw)


# ── Upsert ────────────────────────────────────────────────────────────────────
INSERT_SQL = """
INSERT INTO JSA.WASDE.PSD_SNAPSHOTS
    (release_year, release_month, market_year, commodity,
     country_code, country_name, attribute_id, attribute_name, value)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

DELETE_SQL = """
DELETE FROM JSA.WASDE.PSD_SNAPSHOTS
WHERE  release_year  = %s
  AND  release_month = %s
  AND  commodity     = %s
  AND  country_code  = %s
"""


def upsert(conn, rows: list[tuple], release_year: int, release_month: int,
           commodity: str, country_code: str, dry_run: bool = False) -> int:
    if not rows:
        return 0
    if dry_run:
        for r in rows[:3]:
            print(f"      DRY {r}")
        if len(rows) > 3:
            print(f"      … ({len(rows)} rows total)")
        return len(rows)

    cur = conn.cursor()
    try:
        cur.execute(DELETE_SQL, (release_year, release_month, commodity, country_code))
        cur.executemany(INSERT_SQL, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return len(rows)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    parser = argparse.ArgumentParser(description="WASDE snapshot ETL")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--latest",   action="store_true",
                       help="Pull only the most recent reporting period")
    group.add_argument("--backfill", action="store_true",
                       help="Pull all available reporting periods (~24 months)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print rows, skip DB writes")
    args = parser.parse_args()

    sess = _session()

    print("Fetching available reporting periods…")
    all_periods = get_reporting_periods(sess)
    print(f"  {len(all_periods)} periods available: {all_periods[0]} … {all_periods[-1]}")

    periods = [all_periods[0]] if args.latest else all_periods

    conn = None
    if not args.dry_run:
        print("Connecting to Snowflake…")
        conn = _sf_connect()
        print("  Connected.")

    total_rows = 0
    for rp in periods:
        ry, rm = parse_rp(rp)
        print(f"\n[{rp}] release {ry}-{rm:02d}")
        for commodity, comm_code in COMMODITIES.items():
            for country_label, country_code in COUNTRIES.items():
                print(f"  {commodity} / {country_label}…", end=" ", flush=True)
                try:
                    data = fetch_balance_sheet(sess, comm_code, country_code, rp)
                    rows = build_rows(data, commodity, country_label, country_code, ry, rm)
                    n = upsert(conn, rows, ry, rm, commodity, country_code,
                               dry_run=args.dry_run)
                    print(f"{n} rows")
                    total_rows += n
                except Exception as exc:
                    print(f"ERROR: {exc}")
                time.sleep(0.3)   # polite

    if conn:
        conn.close()
    print(f"\nDone. {total_rows} rows {'(dry run)' if args.dry_run else 'written'}.")


if __name__ == "__main__":
    main()
