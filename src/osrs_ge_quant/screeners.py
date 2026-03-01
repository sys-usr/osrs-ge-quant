from __future__ import annotations

from datetime import timedelta
from typing import Optional, List, Dict, Any

import numpy as np
import pandas as pd
from sqlalchemy import func

from .db import get_session
from .models import PricePoint, Item


def load_daily_matrix(
    years: int = 1,
    timestep: str = "1d_weirdgloop",
) -> pd.DataFrame:
    """
    Load a daily price matrix for screening.

    Returns a DataFrame shaped [ts x item_id].
    """
    session = get_session()
    max_ts = (
        session.query(func.max(PricePoint.ts))
        .filter(PricePoint.timestep == timestep)
        .scalar()
    )
    if max_ts is None:
        session.close()
        return pd.DataFrame()

    start_ts = max_ts - timedelta(days=365 * years)

    rows = (
        session.query(
            PricePoint.item_id,
            PricePoint.ts,
            PricePoint.avg_high,
            PricePoint.avg_low,
            PricePoint.high_vol,
        )
        .filter(
            PricePoint.timestep == timestep,
            PricePoint.ts >= start_ts,
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
                "avg_high": r[2],
                "avg_low": r[3],
                "high_vol": r[4],
            }
            for r in rows
        ]
    )
    df["price"] = df[["avg_high", "avg_low"]].mean(axis=1)
    df = df.dropna(subset=["price"])

    prices = df.pivot(index="ts", columns="item_id", values="price").sort_index()
    return prices


def run_zscore_screener(
    years: int = 1,
    timestep: str = "1d_weirdgloop",
    rolling_window: int = 50,
    min_history: int = 60,
    top_n_by_liquidity: int = 500,
    z_cutoff: Optional[float] = None,
    limit: int = 100,
) -> pd.DataFrame:
    """
    Compute z-score for each item based on rolling mean/std and return a
    summary table of "cheapest" items (lowest z).

    Columns:
        item_id, name, last_price, z_score, median_volume

    If z_cutoff is provided, only include items with z <= z_cutoff.
    """

    prices = load_daily_matrix(years=years, timestep=timestep)
    if prices.empty:
        return pd.DataFrame()

    # require enough history
    valid_mask = prices.notna().sum(axis=0) >= min_history
    prices = prices.loc[:, valid_mask]

    if prices.empty:
        return pd.DataFrame()

    # approximate liquidity from volume
    session = get_session()
    vol_rows = (
        session.query(
            PricePoint.item_id,
            PricePoint.high_vol,
        )
        .filter(
            PricePoint.timestep == timestep,
            PricePoint.ts >= prices.index.min(),
        )
        .all()
    )
    session.close()

    if vol_rows:
        vol_df = pd.DataFrame(
            [{"item_id": r[0], "high_vol": r[1]} for r in vol_rows]
        )
        vol_med = (
            vol_df.groupby("item_id")["high_vol"]
            .median()
            .fillna(0)
            .sort_values(ascending=False)
        )
        liquid_ids = list(vol_med.index[:top_n_by_liquidity])
        prices = prices.loc[:, prices.columns.isin(liquid_ids)]

    # compute z-score on last day
    roll_mean = prices.rolling(rolling_window, min_periods=rolling_window).mean()
    roll_std = prices.rolling(rolling_window, min_periods=rolling_window).std()
    z = (prices - roll_mean) / roll_std

    last_ts = prices.index[-1]
    last_price = prices.loc[last_ts]
    last_z = z.loc[last_ts]

    df = pd.DataFrame(
        {
            "item_id": last_price.index.astype(int),
            "last_price": last_price.values.astype(float),
            "z_score": last_z.values.astype(float),
        }
    )

    # attach names
    session = get_session()
    names = {
        r.id: r.name
        for r in session.query(Item.id, Item.name)
        .filter(Item.id.in_(df["item_id"].tolist()))
        .all()
    }
    session.close()

    df["name"] = df["item_id"].map(names)

    if z_cutoff is not None:
        df = df[df["z_score"] <= z_cutoff]

    df = df.sort_values("z_score").reset_index(drop=True)
    if limit is not None:
        df = df.head(limit)

    return df[
        [
            "item_id",
            "name",
            "last_price",
            "z_score",
        ]
    ]
