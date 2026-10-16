# Weekly agency-estimate refresh (WASDE dashboard · Alt. Groups)

Run weekly. Goal: keep the typed-in / press-sourced agency crop estimates on the dashboard current, without ever
filing a figure you can't verify. Repo: `C:\Users\KoltenPostin\OneDrive - John Stewart and Associates\Desktop\Claude Code\wasde-dashboard`.

## What is already automatic (do NOT touch)
CONAB, MAGyP, Statistics Canada, Eurostat (monthly droplet job), BCR (`etl/agency_reports.py bcr`, droplet), the
Soybean & Corn Advisor (emailed weekly, `etl/pull_advisor_weekly.ps1`). Skip all of these.

## What you refresh (current marketing year only, 2026/27; roll the campaign forward when the season turns)
| Agency | Country | Crops | Where to look |
|---|---|---|---|
| BAGE (Bolsa de Cereales de Buenos Aires) | Argentina | Soybeans, Corn, Wheat | press coverage of its estimates (Reuters, Ámbito, Agrofy, Bichos de Campo, GBM); bolsadecereales.com is behind a bot check: never open it |
| Safras & Mercado | Brazil | Soybeans, Corn | safras.com.br news (Brazil crop estimate posts) |
| SovEcon | Russia | Wheat (also Corn/Barley if stated) | APK-Inform, Interfax, Reuters, sovecon.ru news if reachable |
| IKAR | Russia | Wheat | ikar.ru/1/en/press/ (primary; dated list) |
| UGA (Ukrainian Grain Association) | Ukraine | Wheat, Corn, Barley, Soybeans, Canola | UkrAgroConsult, agroportal.ua, uga.ua |
| ABARES | Australia | Wheat, Barley, Canola | quarterly (Mar/Jun/Sep/Dec; next 1 Dec 2026): agriculture.gov.au crop-report page or press coverage; the report files themselves are behind a bot check: never fetch them |

## Rules (non-negotiable)
1. **Only file a figure you read on a dated page** that names the agency and states the number for that crop and year.
   Fetch the page (WebFetch) and confirm; a search-result snippet is not enough.
2. **report_date = the date of that publication**, not today. `note` = "Via <outlet> <date>; <previous figure/context>".
3. Two sources disagree → file neither, list the conflict in your report.
4. Convert to million tonnes (MMT). Area only if stated: planted_mha / harvested_mha. Never compute a figure.
5. **Never bypass a bot check, CAPTCHA or "verify you are human" page.** If a page is blocked, use press coverage or skip.
6. Don't invent: no agency or crop that isn't in the table; nothing from forecasts of other crop years.

## Procedure
1. For each agency above, search for its newest estimate, then WebFetch the best page and read the figure + date.
2. Write the candidates to a scratch JSON file (format in `etl/agency_upsert.py` docstring), one object per
   country + agency + crop + campaign.
3. Dry run: `python etl/agency_upsert.py <file.json>` and read the verdicts. It rejects: older-than-filed, same,
   >40% from USDA, unknown country/crop, missing note, future date.
4. Re-run with `--write` to file the passing rows.
5. Final message (this is what Kolten reads): a short table of what CHANGED (agency, crop, old -> new, date, source
   link), then anything skipped and why (conflicts, blocked pages, nothing new). If nothing changed, say so in one line.

Use the project's own `.env` (the script loads it). Run from the repo root.
