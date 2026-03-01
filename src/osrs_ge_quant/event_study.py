# src/osrs_ge_quant/event_study.py

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .db import get_session
from .models import PricePoint


def run_event_study(
    event_ts: datetime,
    pre_days: int = 7,
    post_days: int = 7,
    timestep: str = "1d_weirdgloop",
) -> pd.DataFrame:
    """
    Simple cross-sectional event study:

    - Pull prices for [event_ts - pre_days, event_ts + post_days].
    - Build price matrix (date x item_id).
    - Normalize each item by its price on the 'anchor_date' closest to event_ts.
    - For each day offset, compute avg/median relative return across items.

    Returns DataFrame with:
        offset (int days),
        avg_return (float),
        median_return (float)
    """
    session = get_session()
    start = event_ts - timedelta(days=pre_days)
    end = event_ts + timedelta(days=post_days)

    rows = (
        session.query(
            PricePoint.item_id,
            PricePoint.ts,
            PricePoint.avg_high,
            PricePoint.avg_low,
        )
        .filter(
            PricePoint.timestep == timestep,
            PricePoint.ts >= start,
            PricePoint.ts <= end,
        )
        .all()
    )
    session.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                "item_id": r[0],
                "ts": r[1],
                "price": float(r[2] + r[3]) / 2.0,
            }
            for r in rows
        ]
    )

    df["date"] = df["ts"].dt.floor("D")

    # anchor: closest timestamp to the actual event_ts
    idx_closest = (df["ts"] - event_ts).abs().idxmin()
    anchor_ts = df.loc[idx_closest, "ts"]
    anchor_date = anchor_ts.floor("D")

    # Pivot to daily price matrix: date x item_id
    prices = (
        df.pivot_table(
            index="date",
            columns="item_id",
            values="price",
            aggfunc="mean",
        )
        .sort_index()
    )

    if anchor_date not in prices.index:
        # find closest available date
        closest_idx = (prices.index - anchor_date).to_series().abs().idxmin()
        anchor_date = closest_idx

    base = prices.loc[anchor_date]
    base = base.replace(0, np.nan)

    # Relative returns vs event date
    prices_rel = prices.divide(base) - 1.0
    prices_rel = prices_rel.dropna(how="all", axis=1)

    # Offset in days relative to anchor_date
    offsets = (prices_rel.index - anchor_date).days
    prices_rel = prices_rel.copy()
    prices_rel["offset"] = offsets

    # Long-form for aggregation
    long = (
        prices_rel.reset_index()  # index -> 'date'
        .melt(id_vars=["date", "offset"], var_name="item_id", value_name="ret")
    )

    long = long.dropna(subset=["ret"])

    agg = (
        long.groupby("offset")["ret"]
        .agg(avg_return="mean", median_return="median")
        .reset_index()
        .sort_values("offset")
    )

    return agg
