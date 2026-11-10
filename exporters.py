"""'Major Exporters' balance sheet: the top-N exporting countries of a commodity, added together.

The group is chosen by exports in the marketing year the page is looking at (the End year), so it re-forms
whenever the commodity or the year changes. Each country's PSD balance sheet is converted to the page's base
units and summed row by row; yield, domestic use, total use and Stocks-to-Use are then derived from the sums."""
from __future__ import annotations

import pandas as pd

import balance_sheet as bs
import db

LABEL = "Major Exporters (Top 5)"
N_TOP = 5


def top_exporters(code: str, market_year: int, n: int = N_TOP) -> pd.DataFrame:
    """Top n countries by exports in `market_year` (World excluded): country_code, country_name, value."""
    return db.psd_top_exporters(code, int(market_year), n)


def combine(df: pd.DataFrame) -> pd.DataFrame:
    """Sum the member countries' basic rows (base units) by marketing year; yield is rebuilt from the sums."""
    frames = [bs.basics(g) for _, g in df.groupby("country_code")]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).groupby(level=0).sum(min_count=1).sort_index()
    return out.drop(columns=["yield"], errors="ignore")
