"""
Snowflake read layer for the WASDE Streamlit app.

All queries target JSA.WASDE (read-only role: WASDE_ROLE).
Connection is cached per Streamlit session via st.cache_resource.

Environment vars (via st.secrets on Cloud, .env locally):
    SNOWFLAKE_ACCOUNT / USER / ROLE / WAREHOUSE
    SNOWFLAKE_PRIVATE_KEY or SNOWFLAKE_PRIVATE_KEY_PATH [+ SNOWFLAKE_PRIVATE_KEY_PWD]
"""

from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
import streamlit as st


# ── Connection ────────────────────────────────────────────────────────────────
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


def _env(key: str) -> str:
    """Read from st.secrets first, then os.environ."""
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")


@st.cache_resource
def _conn():
    import snowflake.connector
    pk = _load_private_key()
    kw = dict(
        account   = _env("SNOWFLAKE_ACCOUNT"),
        user      = _env("SNOWFLAKE_USER"),
        role      = _env("SNOWFLAKE_ROLE") or "WASDE_ROLE",
        warehouse = _env("SNOWFLAKE_WAREHOUSE"),
        database  = "JSA",
        schema    = "WASDE",
    )
    if pk:
        kw["private_key"] = pk
    else:
        kw["password"] = _env("SNOWFLAKE_PASSWORD")
    kw["client_session_keep_alive"] = True
    return snowflake.connector.connect(**kw)


def _execute(sql: str, params: tuple, fetch: bool):
    """Run a statement; if the cached session has expired, reconnect and retry once."""
    for attempt in (0, 1):
        cur = None
        try:
            cur = _conn().cursor()
            cur.execute(sql, params)
            if not fetch:
                return None
            cols = [d[0].lower() for d in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)
        except Exception:          # incl. connection resets mid-response, not just Snowflake errors
            if attempt:
                raise
            _conn.clear()
        finally:
            try:
                if cur is not None:
                    cur.close()
            except Exception:
                pass


def _query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return _execute(sql, params, fetch=True)


# ── Public API ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def available_releases() -> pd.DataFrame:
    """All (release_year, release_month) combos we have data for."""
    return _query("""
        SELECT DISTINCT release_year, release_month,
               release_year * 100 + release_month AS release_ym
        FROM JSA.WASDE.PSD_SNAPSHOTS
        ORDER BY release_ym DESC
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def available_market_years(commodity: str) -> list[int]:
    df = _query("""
        SELECT DISTINCT market_year
        FROM JSA.WASDE.PSD_SNAPSHOTS
        WHERE commodity = %s
        ORDER BY market_year DESC
    """, (commodity,))
    return df["market_year"].tolist() if not df.empty else []


@st.cache_data(ttl=3600, show_spinner=False)
def latest_balance_sheet(commodity: str, market_year: int,
                         country_code: str = "0000US") -> pd.DataFrame:
    """Latest-vintage balance sheet rows for one commodity × year × country."""
    return _query("""
        SELECT attribute_name, value
        FROM JSA.WASDE.LATEST
        WHERE commodity    = %s
          AND market_year  = %s
          AND country_code = %s
        ORDER BY attribute_id
    """, (commodity, market_year, country_code))


@st.cache_data(ttl=3600, show_spinner=False)
def revision_series(commodity: str, market_year: int,
                    attribute_name: str,
                    country_code: str = "0000US") -> pd.DataFrame:
    """All vintage snapshots for one (commodity, year, attribute, country)."""
    return _query("""
        SELECT release_year, release_month,
               release_year * 100 + release_month AS release_ym,
               value, revision
        FROM JSA.WASDE.REVISIONS
        WHERE commodity      = %s
          AND market_year    = %s
          AND attribute_name = %s
          AND country_code   = %s
        ORDER BY release_ym
    """, (commodity, market_year, attribute_name, country_code))


@st.cache_data(ttl=3600, show_spinner=False)
def multi_attr_revisions(commodity: str, market_year: int,
                         country_code: str = "0000US") -> pd.DataFrame:
    """All attributes' revision series for one commodity × year × country."""
    return _query("""
        SELECT release_year, release_month,
               release_year * 100 + release_month AS release_ym,
               attribute_name, value, revision
        FROM JSA.WASDE.REVISIONS
        WHERE commodity    = %s
          AND market_year  = %s
          AND country_code = %s
        ORDER BY attribute_name, release_ym
    """, (commodity, market_year, country_code))


@st.cache_data(ttl=3600, show_spinner=False)
def tendencies(commodity: str, attribute_name: str,
               country_code: str = "0000US") -> pd.DataFrame:
    """Average revision by release_month across all market years."""
    return _query("""
        SELECT release_month,
               COUNT(*)            AS n_obs,
               AVG(revision)       AS avg_revision,
               STDDEV(revision)    AS std_revision,
               MIN(revision)       AS min_revision,
               MAX(revision)       AS max_revision
        FROM JSA.WASDE.REVISIONS
        WHERE commodity      = %s
          AND attribute_name = %s
          AND country_code   = %s
          AND revision IS NOT NULL
        GROUP BY release_month
        ORDER BY release_month
    """, (commodity, attribute_name, country_code))


@st.cache_data(ttl=3600, show_spinner=False)
def top_countries(commodity: str, market_year: int,
                  attribute_name: str, n: int = 10) -> pd.DataFrame:
    """Top-n countries by attribute for the latest vintage of a market year."""
    return _query("""
        SELECT country_code, country_name, value
        FROM JSA.WASDE.LATEST
        WHERE commodity      = %s
          AND market_year    = %s
          AND attribute_name = %s
          AND country_code  != '0000US'
          AND value         IS NOT NULL
        ORDER BY value DESC
        LIMIT %s
    """, (commodity, market_year, attribute_name, n))


@st.cache_data(ttl=3600, show_spinner=False)
def bucket_history(commodity: str,
                   country_code: str = "0000US") -> pd.DataFrame:
    """Latest-vintage use components and total use across all market years."""
    return _query("""
        SELECT market_year, attribute_name, value
        FROM JSA.WASDE.LATEST
        WHERE commodity    = %s
          AND country_code = %s
          AND attribute_name IN (
              'Feed/Residual Use', 'Food/Seed/Industrial Use',
              'Exports', 'Total Use', 'Ending Stocks',
              'Production', 'Beginning Stocks'
          )
        ORDER BY market_year, attribute_name
    """, (commodity, country_code))


@st.cache_data(ttl=3600, show_spinner=False)
def psd_history(commodity_code: str, country_code: str) -> pd.DataFrame:
    """Full PSD history (1960+) for one commodity and country ('00' = World)."""
    return _query("""
        SELECT market_year, attribute_id, unit_id, value, as_of
        FROM JSA.WASDE.PSD_HISTORY
        WHERE commodity_code = %s AND country_code = %s
        ORDER BY market_year
    """, (commodity_code, country_code))


@st.cache_data(ttl=300, show_spinner=False)
def psd_loaded_commodities() -> list[str]:
    """PSD commodity codes that have history loaded (the first full load fills these in one by one)."""
    return _query("SELECT DISTINCT commodity_code FROM JSA.WASDE.PSD_HISTORY")["commodity_code"].tolist()


@st.cache_data(ttl=3600, show_spinner=False)
def psd_countries(commodity_code: str) -> pd.DataFrame:
    """Countries that have PSD history for a commodity, with display names."""
    return _query("""
        SELECT h.country_code, COALESCE(c.country_name, h.country_code) AS country_name
        FROM (SELECT DISTINCT country_code FROM JSA.WASDE.PSD_HISTORY WHERE commodity_code = %s) h
        LEFT JOIN JSA.WASDE.PSD_COUNTRIES c ON c.country_code = h.country_code
        ORDER BY country_name
    """, (commodity_code,))


@st.cache_data(ttl=3600, show_spinner=False)
def latest_revisions(commodity: str, snapshot_country: str) -> pd.DataFrame:
    """Change in each attribute at the newest WASDE release vs the one before it (US / World, 3 marketing years)."""
    return _query("""
        SELECT market_year, attribute_name, value, revision
        FROM JSA.WASDE.REVISIONS
        WHERE commodity = %s AND country_code = %s
          AND release_ym = (SELECT MAX(release_ym) FROM JSA.WASDE.REVISIONS WHERE commodity = %s)
    """, (commodity, snapshot_country, commodity))


@st.cache_data(ttl=3600, show_spinner=False)
def conab_series(crop: str) -> pd.DataFrame:
    """CONAB historical series (area/yield/production by region and state).

    JSA's own workbooks (CONAB_USER_SERIES, loaded by etl/load_conab_workbooks.py) take priority; CONAB's public
    files fill whatever they lack (the newest season, wheat)."""
    try:
        return _query("""
            SELECT metric, region, level, start_year, label, is_forecast, value FROM (
                SELECT metric, region, level, start_year, label, is_forecast, value
                FROM JSA.WASDE.CONAB_USER_SERIES WHERE crop = %s
                UNION ALL
                SELECT s.metric, s.region, s.level, s.start_year, s.label, s.is_forecast, s.value
                FROM JSA.WASDE.CONAB_SERIES s
                WHERE s.crop = %s AND NOT EXISTS (
                    SELECT 1 FROM JSA.WASDE.CONAB_USER_SERIES u
                    WHERE u.crop = s.crop AND u.metric = s.metric AND u.region = s.region
                      AND u.start_year = s.start_year)
            ) ORDER BY start_year
        """, (crop, crop))
    except Exception:                                   # table not created yet -> public files only
        return _query("""
            SELECT metric, region, level, start_year, label, is_forecast, value
            FROM JSA.WASDE.CONAB_SERIES
            WHERE crop = %s
            ORDER BY start_year
        """, (crop,))


@st.cache_data(ttl=3600, show_spinner=False)
def conab_sd(product: str) -> pd.DataFrame:
    """CONAB supply & demand balance sheet (1,000 t): latest estimate (rev_rank 0) and the prior report (1)."""
    return _query("""
        SELECT item, item_order, season, start_year, rev_rank, rev_label, value, source
        FROM JSA.WASDE.CONAB_SD
        WHERE product = %s
        ORDER BY start_year, item_order
    """, (product,))


@st.cache_data(ttl=3600, show_spinner=False)
def magyp_series(crop: str) -> pd.DataFrame:
    """Argentine ministry (MAGyP) campaign estimates by province."""
    return _query("""
        SELECT campania, start_year, provincia, planted_ha, harvested_ha, production_t
        FROM JSA.WASDE.MAGYP_SERIES
        WHERE crop = %s
        ORDER BY start_year
    """, (crop,))


@st.cache_data(ttl=3600, show_spinner=False)
def statcan_series(crop: str) -> pd.DataFrame:
    """Statistics Canada area / yield / production by province and year."""
    return _query("""
        SELECT geo, year, planted_ha, harvested_ha, production_t, yield_kgha
        FROM JSA.WASDE.STATCAN_SERIES
        WHERE crop = %s
        ORDER BY year
    """, (crop,))


@st.cache_data(ttl=3600, show_spinner=False)
def eurostat_series(crop: str) -> pd.DataFrame:
    """Eurostat crop production (EU standard humidity) for the EU27 and member states."""
    return _query("""
        SELECT geo, geo_name, year, area_kha, production_kt, yield_tha
        FROM JSA.WASDE.EUROSTAT_SERIES
        WHERE crop = %s
        ORDER BY year
    """, (crop,))


@st.cache_data(ttl=120, show_spinner=False)
def agency_estimates(country: str) -> pd.DataFrame:
    """Crop estimates from agencies with no automated feed (BAGE, BCR, UGA, SovEcon, ABARES, ...), typed in by hand."""
    return _query("""
        SELECT id, country, agency, crop, campaign, report_date, planted_mha, production_mmt, yield_tha, note
        FROM JSA.WASDE.AGENCY_ESTIMATES
        WHERE country = %s
        ORDER BY report_date DESC, id DESC
    """, (country,))


def _write(sql: str, params: tuple) -> None:
    _execute(sql, params, fetch=False)
    agency_estimates.clear()


def agency_add(country: str, agency: str, crop: str, campaign: str, report_date,
               planted_mha, production_mmt, yield_tha, note: str) -> None:
    _write("""
        INSERT INTO JSA.WASDE.AGENCY_ESTIMATES
            (country, agency, crop, campaign, report_date, planted_mha, production_mmt, yield_tha, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (country, agency, crop, campaign, report_date, planted_mha, production_mmt, yield_tha, note or None))


def agency_delete(row_id: int) -> None:
    _write("DELETE FROM JSA.WASDE.AGENCY_ESTIMATES WHERE id = %s", (int(row_id),))


def snowflake_available() -> bool:
    """True if Snowflake creds are configured."""
    return bool(_env("SNOWFLAKE_ACCOUNT") and _env("SNOWFLAKE_USER"))
