"""Guarded filing of agency crop estimates (the typed-in / press-sourced ones) into JSA.WASDE.AGENCY_ESTIMATES.

Input is a JSON list of estimates, one object per (country, agency, crop, campaign):
    {"country": "Russia", "agency": "SovEcon", "crop": "Wheat", "campaign": "2026/27", "report_date": "2026-08-24",
     "production_mmt": 88.2, "planted_mha": null, "harvested_mha": null, "yield_tha": null, "note": "Via APK-Inform ..."}

    python etl/agency_upsert.py updates.json            # show what would change, write nothing
    python etl/agency_upsert.py updates.json --write    # file the ones that pass

Every row is checked before it can be filed, and a row that fails is reported and skipped, never "fixed":
  * country / crop must be ones the dashboard has; the agency must be named; the note must name where it came from;
  * report_date must be a real date no later than today, and not older than the newest report already filed
    for that agency + crop + campaign (an old article can't overwrite a newer figure);
  * production must be within 40% of USDA's figure for the same country and marketing year (catches wrong units,
    wrong crop, wrong year). USDA missing -> the check is skipped and the row is flagged "unchecked".
An unchanged figure is left alone (reported as "same"), so a weekly run that finds nothing new writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

COUNTRIES = {"Brazil": "BR", "Argentina": "AR", "United States": "US", "Ukraine": "UP", "Russia": "RS",
             "Australia": "AS", "EU": "E4", "France": None, "Canada": "CA", "Mexico": "MX"}
CROPS = {"Soybeans": "Soybeans", "Corn": "Corn", "Wheat": "Wheat", "Barley": "Barley", "Canola": "Canola (Rapeseed)"}
TOLERANCE = 0.40
FIELDS = ("planted_mha", "harvested_mha", "production_mmt", "yield_tha")


def validate(r: dict, conn) -> tuple[bool, str, dict]:
    """(ok, reason, cleaned row)."""
    try:
        row = dict(country=str(r["country"]).strip(), agency=str(r["agency"]).strip(), crop=str(r["crop"]).strip(),
                   campaign=str(r["campaign"]).strip(), note=(r.get("note") or "").strip(),
                   report_date=datetime.strptime(str(r["report_date"]), "%Y-%m-%d").date())
        for f in FIELDS:
            v = r.get(f)
            row[f] = None if v in (None, "") else float(v)
    except (KeyError, ValueError) as e:
        return False, f"malformed ({e})", {}
    if row["country"] not in COUNTRIES:
        return False, f"unknown country {row['country']!r}", row
    if row["crop"] not in CROPS:
        return False, f"unknown crop {row['crop']!r}", row
    if not row["agency"] or len(row["note"]) < 8:
        return False, "needs an agency name and a note saying where the figure came from", row
    if row["production_mmt"] is None or row["production_mmt"] <= 0:
        return False, "no production figure", row
    if row["report_date"] > date.today():
        return False, "report date is in the future", row
    try:
        my = int(row["campaign"][:4])
    except ValueError:
        return False, f"campaign {row['campaign']!r} isn't like 2026/27", row

    cur = conn.cursor()
    try:
        cur.execute("SELECT report_date, production_mmt FROM JSA.WASDE.AGENCY_ESTIMATES WHERE country=%s AND agency=%s "
                    "AND crop=%s AND campaign=%s ORDER BY report_date DESC LIMIT 1",
                    (row["country"], row["agency"], row["crop"], row["campaign"]))
        have = cur.fetchone()
        if have and have[0] > row["report_date"]:
            return False, f"older than the filed report ({have[0]})", row
        if have and have[0] == row["report_date"] and abs(have[1] - row["production_mmt"]) < 1e-6:
            return False, "same", row
        code, cc = None, COUNTRIES[row["country"]]
        from psd_meta import COMMODITIES
        code = COMMODITIES.get(CROPS[row["crop"]])
        usda = None
        if code and cc:
            cur.execute("SELECT value FROM JSA.WASDE.PSD_HISTORY WHERE commodity_code=%s AND country_code=%s "
                        "AND market_year=%s AND attribute_id=28 LIMIT 1", (code, cc, my))
            hit = cur.fetchone()
            usda = hit[0] / 1000 if hit else None          # PSD is 1,000 MT; estimates are MMT
        if usda:
            dev = row["production_mmt"] / usda - 1
            if abs(dev) > TOLERANCE:
                return False, f"{dev:+.0%} vs USDA {usda:,.1f} MMT: outside the {TOLERANCE:.0%} check", row
            return True, f"ok ({dev:+.1%} vs USDA)", row
        return True, "ok (unchecked: no USDA figure for that year)", row
    finally:
        cur.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    from fetch_wasde import _sf_connect
    items = json.loads(Path(args.file).read_text(encoding="utf-8"))
    conn = _sf_connect()
    good = []
    for r in items:
        ok, why, row = validate(r, conn)
        tag = f"{r.get('country')} {r.get('agency')} {r.get('crop')} {r.get('campaign')} {r.get('production_mmt')}"
        print(f"{'FILE ' if ok else 'SKIP ' if why != 'same' else 'same '} {tag}: {why}")
        if ok:
            good.append(row)
    if args.write and good:
        cur = conn.cursor()
        try:
            for r in good:
                cur.execute("DELETE FROM JSA.WASDE.AGENCY_ESTIMATES WHERE country=%s AND agency=%s AND crop=%s "
                            "AND campaign=%s AND report_date=%s",
                            (r["country"], r["agency"], r["crop"], r["campaign"], r["report_date"]))
                cur.execute("INSERT INTO JSA.WASDE.AGENCY_ESTIMATES (country, agency, crop, campaign, report_date, "
                            "planted_mha, harvested_mha, production_mmt, yield_tha, note) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (r["country"], r["agency"], r["crop"], r["campaign"], r["report_date"], r["planted_mha"],
                             r["harvested_mha"], r["production_mmt"], r["yield_tha"], r["note"]))
            conn.commit()
        finally:
            cur.close()
        print(f"Filed {len(good)}.")
    elif good:
        print(f"{len(good)} would be filed; add --write.")
    else:
        print("Nothing to file.")
    conn.close()


if __name__ == "__main__":
    main()
