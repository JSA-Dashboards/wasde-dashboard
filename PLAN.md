# WASDE Dashboard — Build Plan

## Discovery findings

### Current state
- `wasde-dashboard/app.py` — single-file Streamlit app, 5 tabs, ~970 lines
- Data sources: USDA FAS PSD API (no key, cached 1h), USDA OCE monthly CSV (draft revisions tab)
- **No Snowflake connection at all** — purely API-driven today
- Revisions tab: fetches each WASDE month's CSV on the fly and tries to pivot them; works but is slow (12 serial HTTP calls), fragile (User-Agent trap noted in memory), and limited to 2021+
- Droplet: `jpsi-wasde.service` on port 8508 at `/opt/jpsi/wasde/` with its own `.env` — already deployed

### Snowflake connection pattern (from river-fob-portal and grain-seasonal)
```
USE_SNOWFLAKE = "1"
SNOWFLAKE_ACCOUNT / USER / ROLE / WAREHOUSE
SNOWFLAKE_PRIVATE_KEY or SNOWFLAKE_PRIVATE_KEY_PATH   ← key-pair auth (MFA mandate)
SNOWFLAKE_PRIVATE_KEY_PWD (if key is encrypted)
```
`snowflake.connector` directly — no SQLAlchemy, no `st.connection`.  
`_load_private_key()` pattern from `river-fob-portal/db.py` is the reference.

### Snowflake database to use
**`JSA.WASDE`** — new schema inside the existing `JSA` database (same pattern as `JSA.NASS_CACHE`, `JSA.BASIS_TRACKER`).  
Do NOT set `SNOWFLAKE_SCHEMA` in the app's env (collision risk per memory); pin `schema="WASDE"` at connect time in the WASDE module.

### WASDE CSV notes (from memory)
- URL: `https://www.usda.gov/sites/default/files/documents/oce-wasde-report-data-{year}-{month:02d}.csv`
- Needs `User-Agent: Mozilla/5.0 ...` header (403 without it)
- Available from Aug 2021 onward (~3 marketing years per report)
- Long-format: commodity, item, unit, + one `Value` column per release

### Key architectural question — RESOLVED
> "The PSD API only returns current estimates. Progression/tendency features need every monthly report's numbers kept separately."

**Answer:** store every WASDE CSV release as snapshot rows in Snowflake.  
The USDA FAS PSD API continues to serve the live balance sheet; Snowflake serves the revisions/progression/tendency pages.  
Backfill Aug 2021 → present at build time.

---

## Schema

```sql
-- JSA.WASDE schema (CREATE SCHEMA JSA.WASDE IF NOT EXISTS)

CREATE TABLE IF NOT EXISTS JSA.WASDE.PSD_SNAPSHOTS (
    release_year     INTEGER   NOT NULL,   -- calendar year of the WASDE report
    release_month    INTEGER   NOT NULL,   -- 1-12
    market_year      INTEGER   NOT NULL,   -- USDA marketing year (e.g. 2024 = 2024/25)
    commodity        VARCHAR   NOT NULL,   -- e.g. "Corn", "Soybeans"
    item             VARCHAR   NOT NULL,   -- e.g. "Ending Stocks", "Production"
    unit             VARCHAR,
    region           VARCHAR   NOT NULL DEFAULT 'US',  -- 'US' | 'World' | country name
    value            FLOAT,
    loaded_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (release_year, release_month, market_year, commodity, item, region)
);

-- Fast reads for the revisions tab
CREATE INDEX IF NOT EXISTS idx_snap_comm_item
    ON JSA.WASDE.PSD_SNAPSHOTS (commodity, item, market_year);
```

One row per (release, marketing year, commodity, line item, region).  
A re-load of a month uses `DELETE WHERE release_year=X AND release_month=Y` then `INSERT` (inside a transaction — learned from river-fob `_begin_save`).

---

## Phases

### Phase 1 — Data layer (ETL + views)

**Data source: USDA FAS PSD API** (authenticated — `API_KEY` header)  
Base: `https://apps.fas.usda.gov/opendata/api`  
Endpoint: `GET /psd/commodity/{commodityCode}/country/all/year/{marketYear}`

**Important constraint:** The PSD API returns the **current/latest** estimate for each marketing year — there is no release-month dimension in the response. The ETL stamps `release_year` + `release_month` at run time. This means:
- Balance sheet, country, bucket, and backfill features work immediately
- Revision/tendency history builds forward from the first ETL run; there is no way to reconstruct "what did USDA say in October 2023" via API alone

**1a. ETL script** (`wasde-dashboard/etl/fetch_wasde.py`)
- Call PSD API for each commodity × marketing year (current year + 4 prior)
- Authenticated with `FAS_API_KEY` from environment
- Insert to `JSA.WASDE.PSD_SNAPSHOTS` stamped with today's `release_year`/`release_month` (idempotent: delete this release-month snapshot + re-insert in one transaction)
- `--backfill` flag: pulls all marketing years 2005–present and inserts them all as a single "snapshot" stamped with the current run date (gives historical balance sheet depth; no revision history pre-first-run)
- `--year YYYY` to target a specific marketing year

**1b. Cron** on the Droplet (`/opt/jpsi/wasde/.env` for secrets)
- WASDE releases the 2nd Thursday of each month around 11 AM CT
- Cron: `0 14 * * 4 /opt/jpsi/wasde/venv/bin/python /opt/jpsi/wasde/etl/fetch_wasde.py --latest`  
  (Thursday 2 PM CT = 3 hours after release; re-runs Friday at 9 AM as a catch)
- Wrap both lines in `/opt/alerting/cron-alert`

**1c. Snowflake DDL + views** (`etl/setup_snowflake.py`)
```sql
CREATE SCHEMA IF NOT EXISTS JSA.WASDE;

-- Snapshots: one row per (release, marketing_year, commodity, item, country)
CREATE TABLE IF NOT EXISTS JSA.WASDE.PSD_SNAPSHOTS (
    release_year    INTEGER  NOT NULL,
    release_month   INTEGER  NOT NULL,
    market_year     INTEGER  NOT NULL,
    commodity       VARCHAR  NOT NULL,
    country_code    VARCHAR  NOT NULL DEFAULT '0000US',
    country_name    VARCHAR,
    attribute_id    INTEGER,
    attribute_name  VARCHAR  NOT NULL,
    unit            VARCHAR,
    value           FLOAT,
    loaded_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (release_year, release_month, market_year, commodity, country_code, attribute_name)
);

-- Latest vintage only (max release per market_year)
CREATE OR REPLACE VIEW JSA.WASDE.LATEST AS
SELECT * FROM JSA.WASDE.PSD_SNAPSHOTS s
WHERE (release_year, release_month) = (
    SELECT MAX(release_year), MAX(release_month)  -- simplified; real: rank by release date
    FROM JSA.WASDE.PSD_SNAPSHOTS
    WHERE commodity = s.commodity AND market_year = s.market_year
);
```

**1d. Roles** (`etl/setup_snowflake.py`)
```sql
CREATE ROLE IF NOT EXISTS WASDE_ROLE;
GRANT USAGE ON DATABASE JSA TO ROLE WASDE_ROLE;
GRANT USAGE ON SCHEMA JSA.WASDE TO ROLE WASDE_ROLE;
GRANT SELECT ON ALL TABLES IN SCHEMA JSA.WASDE TO ROLE WASDE_ROLE;
GRANT SELECT ON ALL VIEWS IN SCHEMA JSA.WASDE TO ROLE WASDE_ROLE;
-- ETL writer role (separate service user):
CREATE ROLE IF NOT EXISTS WASDE_ETL_ROLE;
GRANT INSERT, DELETE, SELECT ON TABLE JSA.WASDE.PSD_SNAPSHOTS TO ROLE WASDE_ETL_ROLE;
```

**1e. Unit handling** (`wasde-dashboard/units.py`)  
Store raw PSD units (1000 MT); display layer converts on demand:  
- 1,000 MT corn → 39.368M bu (factor 39.368)  
- 1,000 MT soybeans → 36.744M bu  
- 1,000 MT wheat → 36.744M bu  
Toggle in app sidebar: Metric (1000 MT) / Imperial (million bu)

---

### Phase 2 — App redesign (5 pages)

Replace single-file app with **multi-page Streamlit** (pages/ directory).  
Style: River FOB Portal's clean sans-serif card look (per memory feedback) + JSA green `#5e7164` / `#8db89a`.

**Sidebar** (always visible)
- JSA logo + "WASDE Dashboard" heading
- Commodity selector (Corn / Soybeans / Wheat / Cotton / Rice / Soybean Oil)
- Marketing year selector (populated from Snowflake)
- US / World toggle

**Page 1: Balance Sheet** (`pages/1_Balance_Sheet.py`)
- KPI row: Production, Exports, Total Use, Ending Stocks, Stocks/Use %
- 3-year side-by-side supply/demand table (current + 2 prior marketing years)
- Production vs Use bar chart + Ending Stocks line overlay
- Source: USDA FAS PSD API (live; no Snowflake needed here)

**Page 2: WASDE Progression** (`pages/2_Progression.py`)
- Line chart: how each WASDE estimate evolved month-by-month through the crop year
- Tracked items selectable: Production / Ending Stocks / Exports / Total Use
- Show prior-year progression for comparison (dashed)
- Month-over-month delta table (green up / red down)
- Source: Snowflake `PSD_SNAPSHOTS`

**Page 3: Bucket Trends** (`pages/3_Bucket_Trends.py`)
- Multi-year stacked area: how each use component (Feed, FSI, Exports) has shifted as a share of total use
- "Bucket" = % share of total, so a growing export share is immediately visible
- Side-by-side bar for selected year vs 5-year avg
- Source: Snowflake + PSD API

**Page 4: Country View** (`pages/4_Countries.py`)
- Horizontal bar: top producers / exporters for selected year
- Stacked bar: top 5 countries' production over time
- Ending stocks heatmap by country × year
- Source: PSD API (world data)

**Page 5: WASDE Tendencies** (`pages/5_Tendencies.py`)
- "What does USDA typically do in the October WASDE?"
- For each item, compute avg revision (vs prior month) by release month across all years
- Table: avg Oct revision for Production, Ending Stocks, Exports since 2021
- Chart: revision distribution (box plot) by month
- Source: Snowflake `PSD_SNAPSHOTS`

---

### Phase 3 — Chatbot

**`pages/6_Ask_AI.py`**  
Pattern: same as `grain-seasonal-dashboard/ai_chat.py` (Claude API via `st.secrets["ANTHROPIC_API_KEY"]`).

**Fixed query tools** (not open-ended SQL — read-only, no injection risk):
- `get_balance_sheet(commodity, marketing_year, region)` → current estimates
- `get_revision_series(commodity, item, marketing_year)` → month-by-month progression
- `get_tendency(commodity, item, release_month)` → avg historical revision
- `get_top_producers(commodity, marketing_year, n)` → top-n countries

Model: `claude-sonnet-4-6`. System prompt grounds it on WASDE concepts (marketing year conventions, US vs world scope, etc.) and forbids it from making price predictions.

---

### Phase 4 — Polish

- `requirements.txt` with pinned versions (streamlit, plotly, pandas, requests, snowflake-connector-python, anthropic, cryptography)
- `README.md`: setup, env vars, backfill command, cron setup
- Basic smoke tests (`tests/test_units.py`, `tests/test_etl_parse.py`) — no AppTest (Snowflake not available in CI)
- Remove the old single-file `app.py` and point the systemd service at the new `app.py` entry point

---

## Decisions locked in

1. **Data source:** USDA FAS PSD API only (authenticated with `FAS_API_KEY`); no WASDE CSV
2. **Anthropic API key:** already in droplet `.env`
3. **Snowflake role:** new `WASDE_ROLE` (read-only) + `WASDE_ETL_ROLE` (insert/delete on snapshots)
4. **Backfill:** pull all marketing years 2005–present via PSD API, stamp with current run date; revision history builds forward from first ETL run

---

## Effort estimate

| Phase | Work |
|-------|------|
| 1 — Data layer | ~2–3h: ETL script, Snowflake DDL, backfill run, cron |
| 2 — App redesign | ~4–5h: 5 pages, styling, charts |
| 3 — Chatbot | ~2h: tool definitions, system prompt, page |
| 4 — Polish | ~1h: tests, README, wiring |

**Recommend reviewing this PLAN.md before proceeding.** Phase 1 touches Snowflake and the droplet cron; get sign-off on the schema and role first.
