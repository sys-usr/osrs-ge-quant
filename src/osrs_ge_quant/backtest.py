# src/osrs_ge_quant/backtest.py
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from sqlalchemy import select

from .db import get_session
from .models import PricePoint, Item
from .strategy import generate_flip_recommendations
from .config import load_settings

def load_history_window(start: datetime, end: datetime, timestep: str = "1h") -> pd.DataFrame:
    session = get_session()
    stmt = (
        select(PricePoint, Item)
        .join(Item, PricePoint.item_id == Item.id)
        .where(PricePoint.ts >= start, PricePoint.ts < end, PricePoint.timestep == timestep)
    )
    rows = session.execute(stmt).all()
    data = []
    for pp, it in rows:
        data.append({
            "ts": pp.ts,
            "item_id": it.id,
            "name": it.name,
            "avgHighPrice": pp.avg_high,
            "avgLowPrice": pp.avg_low,
            "highPriceVolume": pp.high_vol,
            "lowPriceVolume": pp.low_vol,
            "limit": it.limit,
        })
    return pd.DataFrame(data)

def backtest_flip_strategy():
    settings = load_settings()
    years = settings["analysis"]["backtest_years"]
    end = datetime.utcnow()
    start = end - timedelta(days=365*years)

    current = start
    capital = 100_000_000
    pnl = 0.0

    while current < end:
        window_end = current + timedelta(hours=1)
        df = load_history_window(current, window_end)
        if df.empty:
            current = window_end
            continue
        # Snapshot per item using last row in window
        snap = df.sort_values("ts").groupby("item_id").tail(1)

        recs = generate_flip_recommendations(snap)
        # Very naive: allocate small cap per rec and assume you buy at avgHigh and sell at avgLow next hour
        for _, r in recs.head(10).iterrows():
            qty = min(r["suggested_qty"], 1000)
            buy = r["avgHighPrice"] * qty
            if buy > capital * 0.05:
                continue
            sell = r["avgLowPrice"] * qty * (1 - settings["ge"]["tax_rate"])
            pnl += sell - buy
            capital += sell - buy

        current = window_end

    return {"start": start, "end": end, "final_capital": capital, "pnl": pnl}
