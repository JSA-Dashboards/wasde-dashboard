"""Load the weekly 'Soybean & Corn Advisor' report (Brazil / Argentina / U.S. corn + soybean estimates) into
JSA.WASDE.AGENCY_ESTIMATES so it shows beside USDA on the Alternative Groups page.

Run by hand each week with the emailed attachment (a .docx, or the .msg that carries it):
    python etl/load_advisor_report.py "C:\\path\\October 12, 2026.docx" [--dry-run]

Idempotent per report date: a rerun replaces that week's Advisor rows. Only the report's own estimate lines are
read (planted area, production, yield, plus the min-max range in the note); USDA's figures come from PSD, not
from the report.
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from fetch_wasde import _sf_connect  # noqa: E402

AGENCY = "Soybean & Corn Advisor"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HA_PER_ACRE = 0.404685642
MT_PER_BU = {"Corn": 0.0254016, "Soybeans": 0.0272155}          # 56 lb and 60 lb bushels
CROPS = {"soybean": "Soybeans", "corn": "Corn"}
SA = {"Brazil": "Brazil", "Argentina": "Argentina"}


def read_lines(path: Path) -> list[str]:
    data = path.read_bytes()
    if path.suffix.lower() == ".msg":                    # Outlook message: take its .docx attachment
        import extract_msg
        msg = extract_msg.openMsg(str(path))
        att = next(a for a in msg.attachments if (a.data or b"")[:2] == b"PK")      # a .docx is a zip
        data = att.data
    import io
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    lines = []
    for p in root.iter(W + "p"):
        t = "".join(x.text or "" for x in p.iter(W + "t")).strip()
        if t:
            lines.append(re.sub(r"\s+", " ", t.replace("\u00a0", " ")))
    return lines


def report_date(lines: list[str]) -> date:
    for ln in lines[:8]:
        m = re.match(r"([A-Z][a-z]+) (\d{1,2}), (20\d\d)", ln)
        if m:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    raise ValueError("report date not found in the first lines")


def parse(lines: list[str], rdate: date) -> list[dict]:
    rows: dict[tuple, dict] = {}

    def row(country, crop, campaign):
        return rows.setdefault((country, crop, campaign), {
            "country": country, "agency": AGENCY, "crop": crop, "campaign": campaign,
            "report_date": rdate, "planted_mha": None, "harvested_mha": None, "production_mmt": None, "yield_tha": None, "note": ""})

    # South America: "2026/27 Brazil soybean planted acreage49.2 mha (121.5 mac), up 1%" / "... production180.0 mt, ..."
    for ln in lines:
        m = re.match(r"(20\d\d/\d\d) (Brazil|Argentina) (soybean|corn) planted acreage\s*([\d.]+) mha", ln)
        if m:
            row(m.group(2), CROPS[m.group(3)], m.group(1))["planted_mha"] = float(m.group(4))
        m = re.match(r"(20\d\d/\d\d) (Brazil|Argentina) (soybean|corn) production\s*([\d.]+) (?:mt|million)", ln)
        if m:
            row(m.group(2), CROPS[m.group(3)], m.group(1))["production_mmt"] = float(m.group(4))

    # Range table: "Brazil 180.0 185.0 170.0 186.0 - 180.5" = estimate, maximum, minimum, USDA, Conab, last year's USDA
    section = None
    for ln in lines:
        m = re.match(r"(20\d\d/\d\d) South American (Soybean|Corn) Production", ln)
        if m:
            section = (m.group(1), CROPS[m.group(2).lower()])
            continue
        if section and (m := re.match(r"(Brazil|Argentina)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+", ln)):
            est, hi, lo = (float(m.group(i)) for i in (2, 3, 4))
            r = row(m.group(1), section[1], section[0])
            if r["production_mmt"] is None:
                r["production_mmt"] = est
            r["note"] = f"Weekly range {lo:g}-{hi:g} MMT"
        if section and ln.startswith("Total"):
            section = None

    # United States: "2026 U.S. corn planted acreage96.77 million acres" etc. (Advisor's own USDA-adjusted yield)
    us = {}
    for ln in lines:
        m = re.match(r"(20\d\d) U\.S\. (corn|soybean) (planted acreage|harvested acreage|yield|production)\s*"
                     r"([\d.]+)\s*(million acres|bushels per acre|billion bushels)", ln)
        if m:
            us[(m.group(2), m.group(3))] = (float(m.group(4)), int(m.group(1)))
    for word, crop in CROPS.items():
        if (word, "production") not in us:
            continue
        val, yr = us[(word, "production")]
        r = row("United States", crop, f"{yr}/{str(yr + 1)[-2:]}")
        r["production_mmt"] = val * 1000 * MT_PER_BU[crop]          # billion bu -> million t
        if (word, "planted acreage") in us:
            r["planted_mha"] = us[(word, "planted acreage")][0] * HA_PER_ACRE
        if (word, "harvested acreage") in us:
            r["harvested_mha"] = us[(word, "harvested acreage")][0] * HA_PER_ACRE
        if (word, "yield") in us:
            r["yield_tha"] = us[(word, "yield")][0] * MT_PER_BU[crop] / HA_PER_ACRE * 1.0
        if (word, "harvested acreage") in us:
            r["note"] = f"Harvested {us[(word, 'harvested acreage')][0]:.2f} M ac; {us[(word, 'yield')][0]:g} bu/ac"
    return [r for r in rows.values() if r["production_mmt"] is not None]


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help=".docx attachment (or the .msg that carries it)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lines = read_lines(Path(args.file))
    rdate = report_date(lines)
    rows = parse(lines, rdate)
    print(f"Report dated {rdate}: {len(rows)} estimates")
    for r in rows:
        print(f"  {r['country']:14} {r['crop']:9} {r['campaign']}  prod {r['production_mmt']:8.2f} MMT"
              f"  planted {r['planted_mha'] if r['planted_mha'] is None else round(r['planted_mha'], 2)} M ha"
              f"  yield {r['yield_tha'] if r['yield_tha'] is None else round(r['yield_tha'], 2)}  {r['note']}")
    if args.dry_run:
        print("Dry run: nothing written.")
        return
    conn = _sf_connect()
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE JSA.WASDE.AGENCY_ESTIMATES ADD COLUMN IF NOT EXISTS harvested_mha FLOAT")
        cur.execute("DELETE FROM JSA.WASDE.AGENCY_ESTIMATES WHERE agency = %s AND report_date = %s",
                    (AGENCY, rdate))
        cur.executemany(
            "INSERT INTO JSA.WASDE.AGENCY_ESTIMATES (country, agency, crop, campaign, report_date, planted_mha, "
            "harvested_mha, production_mmt, yield_tha, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [(r["country"], AGENCY, r["crop"], r["campaign"], r["report_date"], r["planted_mha"],
              r["harvested_mha"], r["production_mmt"], r["yield_tha"], r["note"] or None) for r in rows])
        conn.commit()
    finally:
        cur.close()
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
