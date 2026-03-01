# src/osrs_ge_quant/backtest/loaders.py

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Optional, List

import pandas as pd
from sqlalchemy import select, and_

from ..db import get_session
from ..models import PricePoint, Item


def load_price_history(
    item_ids: Optional[Iterable[int]] = None,
    timestep: str = "1h",
    years: int = 1,
) -> pd.DataFrame:
    """
    Load historical price data from the prices table for backtesting.

    Returns DataFrame with columns:
      - ts (datetime)
      - item_id
      - name
      - avg_high
      - avg_low
      - high_vol
      - low_vol
      - timestep
    """
    session = get_session()

    end = datetime.utcnow()
    start = end - timedelta(days=365 * years)

    stmt = (
        select(PricePoint, Item)
        .join(Item, Item.id == PricePoint.item_id)
        .where(
            and_(
                PricePoint.ts >= start,
                PricePoint.ts <= end,
                PricePoint.timestep == timestep,
            )
        )
    )

    if item_ids:
        item_ids = list(item_ids)
        if item_ids:
            stmt = stmt.where(PricePoint.item_id.in_(item_ids))

    rows = session.execute(stmt).all()

    data: List[dict] = []
    for pp, it in rows:
        data.append(
            {
                "ts": pp.ts,
                "item_id": it.id,
                "name": it.name,
                "avg_high": pp.avg_high,
                "avg_low": pp.avg_low,
                "high_vol": pp.high_vol,
                "low_vol": pp.low_vol,
                "timestep": pp.timestep,
            }
        )

    df = pd.DataFrame(data)
    if df.empty:
        return df

    df.sort_values(["item_id", "ts"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
