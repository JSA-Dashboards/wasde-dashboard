"""
Create the JSA.WASDE schema, tables, views, and roles.

Run once (idempotent) as ACCOUNTADMIN or a role with CREATE SCHEMA / ROLE privs:

    python etl/setup_snowflake.py

Reads Snowflake creds from environment (same vars as the app):
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_ROLE, SNOWFLAKE_WAREHOUSE
    SNOWFLAKE_PRIVATE_KEY or SNOWFLAKE_PRIVATE_KEY_PATH [+ SNOWFLAKE_PRIVATE_KEY_PWD]
"""

import os
import sys
from pathlib import Path

# Allow running from the repo root or the etl/ subfolder
sys.path.insert(0, str(Path(__file__).parent.parent))


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


def connect():
    import snowflake.connector
    pk = _load_private_key()
    kw = dict(
        account   = os.environ["SNOWFLAKE_ACCOUNT"],
        user      = os.environ["SNOWFLAKE_USER"],
        role      = os.environ.get("SNOWFLAKE_ROLE"),
        warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE"),
        database  = "JSA",
    )
    if pk:
        kw["private_key"] = pk
    else:
        kw["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**kw)


DDL = [
    # ── Schema ────────────────────────────────────────────────────────────────
    "CREATE SCHEMA IF NOT EXISTS JSA.WASDE",

    # ── Snapshot table ────────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS JSA.WASDE.PSD_SNAPSHOTS (
        release_year    INTEGER   NOT NULL,
        release_month   INTEGER   NOT NULL,
        market_year     INTEGER   NOT NULL,
        commodity       VARCHAR   NOT NULL,
        country_code    VARCHAR   NOT NULL,
        country_name    VARCHAR,
        attribute_id    VARCHAR,
        attribute_name  VARCHAR   NOT NULL,
        value           FLOAT,
        loaded_at       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (release_year, release_month, market_year, commodity,
                     country_code, attribute_name)
    )
    """,

    # ── Latest-vintage view ───────────────────────────────────────────────────
    # Most recent release snapshot per (commodity, market_year, country, attribute)
    """
    CREATE OR REPLACE VIEW JSA.WASDE.LATEST AS
    SELECT s.*
    FROM JSA.WASDE.PSD_SNAPSHOTS s
    INNER JOIN (
        SELECT commodity, market_year, country_code, attribute_name,
               MAX(release_year * 100 + release_month) AS max_ym
        FROM JSA.WASDE.PSD_SNAPSHOTS
        GROUP BY 1, 2, 3, 4
    ) m ON s.commodity       = m.commodity
       AND s.market_year     = m.market_year
       AND s.country_code    = m.country_code
       AND s.attribute_name  = m.attribute_name
       AND (s.release_year * 100 + s.release_month) = m.max_ym
    """,

    # ── Revision series view ──────────────────────────────────────────────────
    # All vintages — used by the Progression and Tendencies pages
    """
    CREATE OR REPLACE VIEW JSA.WASDE.REVISIONS AS
    SELECT
        release_year,
        release_month,
        release_year * 100 + release_month  AS release_ym,
        market_year,
        commodity,
        country_code,
        country_name,
        attribute_name,
        value,
        LAG(value) OVER (
            PARTITION BY commodity, market_year, country_code, attribute_name
            ORDER BY release_year * 100 + release_month
        ) AS prev_value,
        value - LAG(value) OVER (
            PARTITION BY commodity, market_year, country_code, attribute_name
            ORDER BY release_year * 100 + release_month
        ) AS revision
    FROM JSA.WASDE.PSD_SNAPSHOTS
    """,

    # ── Roles ─────────────────────────────────────────────────────────────────
    "CREATE ROLE IF NOT EXISTS WASDE_ROLE",
    "CREATE ROLE IF NOT EXISTS WASDE_ETL_ROLE",
    "GRANT USAGE ON DATABASE JSA TO ROLE WASDE_ROLE",
    "GRANT USAGE ON DATABASE JSA TO ROLE WASDE_ETL_ROLE",
    "GRANT USAGE ON SCHEMA JSA.WASDE TO ROLE WASDE_ROLE",
    "GRANT USAGE ON SCHEMA JSA.WASDE TO ROLE WASDE_ETL_ROLE",
    "GRANT SELECT ON ALL TABLES IN SCHEMA JSA.WASDE TO ROLE WASDE_ROLE",
    "GRANT SELECT ON ALL VIEWS  IN SCHEMA JSA.WASDE TO ROLE WASDE_ROLE",
    "GRANT SELECT, INSERT, DELETE ON TABLE JSA.WASDE.PSD_SNAPSHOTS TO ROLE WASDE_ETL_ROLE",
    "GRANT SELECT ON ALL VIEWS  IN SCHEMA JSA.WASDE TO ROLE WASDE_ETL_ROLE",
]


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")

    conn = connect()
    cur  = conn.cursor()
    for stmt in DDL:
        stmt = stmt.strip()
        if not stmt:
            continue
        print(f"  {stmt[:72].replace(chr(10),' ')}…")
        cur.execute(stmt)
        conn.commit()
    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
