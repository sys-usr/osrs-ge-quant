# src/osrs_ge_quant/engine.py (core part)
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select

from .db import get_session
from .models import Item, PricePoint, Recommendation
from .config import load_settings
from .strategy import generate_flip_recommendations, generate_processing_recommendations


def run_full_cycle():
    from .ge_api import refresh_universe

    settings = load_settings()
    refresh_universe(snapshot_timestep=settings["ge"]["default_timestep"])

    session = get_session()

    stmt = select(PricePoint, Item).join(Item, PricePoint.item_id == Item.id)
    rows = session.execute(stmt).all()
    if not rows:
        return

    data = []
    for pp, it in rows:
        data.append(
            {
                "item_id": it.id,
                "id": it.id,
                "name": it.name,
                "limit": it.limit,
                "members": it.members,
                "avgHighPrice": pp.avg_high,
                "avgLowPrice": pp.avg_low,
                "highPriceVolume": pp.high_vol,
                "lowPriceVolume": pp.low_vol,
            }
        )
    df = pd.DataFrame(data).dropna(subset=["avgHighPrice", "avgLowPrice"])

    flips = generate_flip_recommendations(df)
    processing = generate_processing_recommendations(df)

    # Persist recommendations
    for _, r in flips.iterrows():
        rec = Recommendation(
            strategy_name=r["strategy_name"],
            item_id=int(r["item_id"]),
            side="buy",
            qty=int(r["suggested_qty"]),
            price_each=int(r["buy_price"]),
            expected_profit_gp=float(r["expected_profit_gp"]),
            expected_return_pct=float(r["expected_return_pct"]),
            signal_type="pure_flip",
            reason="margin flip",
        )
        session.add(rec)

    for _, r in processing.iterrows():
        rec = Recommendation(
            strategy_name=r["strategy_name"],
            item_id=None,
            side=None,
            qty=None,
            price_each=None,
            expected_profit_gp=float(r["profit_per_batch"])
            if "profit_per_batch" in r
            else None,
            expected_return_pct=None,
            signal_type="processing",
            reason=r["recipe_name"],
        )
        session.add(rec)

    session.commit()

