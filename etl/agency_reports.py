"""Read agency crop-estimate reports (PDF / Word / HTML) and file the figures in JSA.WASDE.AGENCY_ESTIMATES.

Agencies whose sites we can reach are fetched automatically; for the ones that block scripts (BAGE, ABARES, FAS
attaché reports ...) save the report you receive and pass it with --file. Either way the figures are read by
per-agency rules, checked against plausible ranges, and PRINTED WITH THEIR EVIDENCE SENTENCE first. Nothing is
written unless you add --write, and a figure that fails its range check is dropped, never guessed.

    python etl/agency_reports.py bcr                       # fetch BCR's latest monthly report, show what it found
    python etl/agency_reports.py bcr --write               # ... and load it
    python etl/agency_reports.py bcr --file report.pdf --date 2026-09-09 --write

Adding an agency = one function in EXTRACTORS (text -> list of estimate dicts) plus, if its site allows scripts,
one fetcher in FETCHERS.
"""
from __future__ import annotations

import argparse
import html as _html
import io
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

UA = {"User-Agent": "Mozilla/5.0 (JPSI-WASDE-ETL)"}
PDFTOTEXT = shutil.which("pdftotext") or r"C:\Program Files\Git\mingw64\bin\pdftotext.exe"


# ── text extraction ───────────────────────────────────────────────────────────
def read_text(path: Path) -> str:
    """Plain text in reading order (so two-column PDFs don't interleave), whitespace-normalised."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:                                                  # PyMuPDF: pure pip, same reading order as pdftotext -raw
            try:
                import pymupdf as fitz
            except ImportError:                               # older PyMuPDF still calls itself fitz
                import fitz
            with fitz.open(path) as doc:
                text = " ".join(page.get_text("text") for page in doc)
        except ImportError:
            out = subprocess.run([PDFTOTEXT, "-raw", "-enc", "UTF-8", str(path), "-"], capture_output=True, check=True)
            text = out.stdout.decode("utf-8", "ignore")
    elif suffix == ".docx":
        import xml.etree.ElementTree as ET
        w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
        text = "\n".join("".join(t.text or "" for t in p.iter(w + "t")) for p in root.iter(w + "p"))
    else:                                                    # .htm / .html / .txt
        raw = path.read_text(encoding="utf-8", errors="ignore")
        raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S)
        text = _html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"-\n(?=[a-záéíóúñ])", "", text)           # re-join hyphenated line breaks
    return re.sub(r"\s+", " ", text).strip()


def num(s: str) -> float:
    return float(s.replace(" ", "").replace(".", "").replace(",", ".")) if "," in s else float(s.replace(" ", ""))


# ── BCR (Bolsa de Comercio de Rosario) ────────────────────────────────────────
BCR_GEA = "https://www.bcr.com.ar/es/mercados/gea/estimaciones-nacionales-de-produccion/estimaciones"
# crop word -> (campaign style, plausible production Mt, plausible area M ha)
BCR_CROPS = {"soja": ("Soybeans", (20, 70), (10, 25)), "maíz": ("Corn", (30, 95), (5, 16)),
             "trigo": ("Wheat", (8, 30), (3, 9))}


def bcr_fetch() -> tuple[Path, date, str]:
    page = requests.get(BCR_GEA, headers=UA, timeout=60).text
    m = re.search(r'href="([^"]*informe_especial_(\d+)_(\d{4})_(\d{2})_(\d{2})\.pdf)"', page)
    if not m:
        raise RuntimeError("no informe_especial PDF linked on BCR's estimates page")
    url, n, y, d1, d2 = m.group(1), m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5))
    rdate = date(y, d2, d1)                                  # file names are yyyy_dd_mm
    tmp = Path(tempfile.mkdtemp(prefix="bcr_"))
    f = tmp / url.rsplit("/", 1)[-1]
    f.write_bytes(requests.get(url, headers=UA, timeout=120).content)
    return f, rdate, f"BCR Informe especial mensual N° {n} ({rdate:%d/%m/%Y})"


def bcr_extract(text: str, rdate: date, label: str) -> list[dict]:
    """Production comes from 'horizonte productivo de X Mt' / 'podría dejar X Mt' / 'proyección ... a X Mt';
    the crop is the nearest crop word before the figure, area/yield the nearest M ha / qq/ha before it."""
    found: dict[str, dict] = {}
    pats = [r"horizonte productivo (?:de|en|se ubica en) ([\d.,]+) Mt", r"podr[ií]a dejar ([\d.,]+) Mt",
            r"(?:proyecci[oó]n|producci[oó]n)[^.]{0,40}?(?:pasa|sube|baja|queda)[^.]{0,30}? a ([\d.,]+) Mt"]
    for pat in pats:
        for m in re.finditer(pat, text, flags=re.I):
            win = text[max(0, m.start() - 900):m.start()]
            kws = [(win.lower().rfind(k), k) for k in BCR_CROPS if k in win.lower()]
            if not kws:
                continue
            crop_word = max(kws)[1]
            crop, (plo, phi), (alo, ahi) = BCR_CROPS[crop_word]
            prod = num(m.group(1))
            if not plo <= prod <= phi or crop in found:     # out of range, or the crop's first (headline) figure wins
                continue
            area = None                                      # planted: nearest "X M ha" that isn't the harvested (cosecha) one
            for am in reversed(list(re.finditer(r"(\d+(?:, ?\d+)?)\s*M ha", win))):
                if "cosecha" in win[max(0, am.start() - 70):am.start()].lower():
                    continue
                if alo <= num(am.group(1)) <= ahi:
                    area = num(am.group(1))
                    break
            harv = re.findall(r"cosecha para grano comercial de (\d+(?:, ?\d+)?) M ha", text) if crop == "Corn" else []
            yq = re.findall(r"([\d]+,\d)\s*qq/ha", win)
            evidence = text[max(0, m.start() - 80):m.end() + 40]
            found[crop] = dict(country="Argentina", agency="BCR", crop=crop, campaign="2026/27" if rdate.year == 2026
                               else f"{rdate.year}/{str(rdate.year + 1)[-2:]}",
                               report_date=rdate, production_mmt=prod, planted_mha=area,
                               harvested_mha=num(harv[0]) if harv else None,
                               yield_tha=num(yq[-1]) / 10 if yq else None, note=label, evidence=evidence)
    return list(found.values())


EXTRACTORS = {"bcr": bcr_extract}
FETCHERS = {"bcr": bcr_fetch}


# ── filing ────────────────────────────────────────────────────────────────────
def write(rows: list[dict]):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    from fetch_wasde import _sf_connect
    conn = _sf_connect()
    cur = conn.cursor()
    try:
        for r in rows:
            cur.execute("DELETE FROM JSA.WASDE.AGENCY_ESTIMATES WHERE country=%s AND agency=%s AND crop=%s "
                        "AND campaign=%s AND report_date=%s",
                        (r["country"], r["agency"], r["crop"], r["campaign"], r["report_date"]))
            cur.execute(
                "INSERT INTO JSA.WASDE.AGENCY_ESTIMATES (country, agency, crop, campaign, report_date, planted_mha, "
                "harvested_mha, production_mmt, yield_tha, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (r["country"], r["agency"], r["crop"], r["campaign"], r["report_date"], r["planted_mha"],
                 r["harvested_mha"], r["production_mmt"], r["yield_tha"], r["note"]))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=sorted(EXTRACTORS))
    ap.add_argument("--file", help="a saved report (.pdf / .docx / .html) instead of fetching")
    ap.add_argument("--date", help="report date YYYY-MM-DD (required with --file)")
    ap.add_argument("--write", action="store_true", help="load the figures (default: show only)")
    args = ap.parse_args()

    if args.file:
        if not args.date:
            sys.exit("--date is required with --file")
        path, rdate, label = Path(args.file), date.fromisoformat(args.date), Path(args.file).name
    else:
        path, rdate, label = FETCHERS[args.source]()
    rows = EXTRACTORS[args.source](read_text(path), rdate, label)
    print(f"{args.source.upper()} report dated {rdate} ({label}): {len(rows)} estimates")
    for r in rows:
        print(f"  {r['country']:10} {r['crop']:9} {r['campaign']}  prod {r['production_mmt']:6.2f} MMT"
              f"  planted {r['planted_mha']} harvested {r['harvested_mha']} yield {r['yield_tha']} M ha / t/ha")
        print(f"      evidence: …{r['evidence']}…")
    if not rows:
        print("  Nothing passed the range checks: nothing to load.")
        return
    if args.write:
        write(rows)
        print("Written.")
    else:
        print("Show-only run: add --write to load these.")


if __name__ == "__main__":
    main()
