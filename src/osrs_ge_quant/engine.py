# src/osrs_ge_quant/engine.py (core part)
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy import select, func

from .db import get_session
from .models import Item, PricePoint, Recommendation
from .config import load_settings
from .strategy import generate_flip_recommendations, generate_processing_recommendations


def run_full_cycle(send_digest: bool = True):
    from .ge_api import refresh_universe

    settings = load_settings()
    default_timestep = settings["ge"].get("default_timestep", "24h")
    refresh_universe(snapshot_timestep=default_timestep)

    session = get_session()

    # Find the latest timestamp for the default timestep to avoid scanning the entire historical database
    latest_ts = (
        session.query(func.max(PricePoint.ts))
        .filter(PricePoint.timestep == default_timestep)
        .scalar()
    )

    if latest_ts is None:
        session.close()
        return

    stmt = (
        select(PricePoint, Item)
        .join(Item, PricePoint.item_id == Item.id)
        .where(
            PricePoint.timestep == default_timestep,
            PricePoint.ts == latest_ts,
        )
    )
    rows = session.execute(stmt).all()
    if not rows:
        session.close()
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
    min_profit_hot = settings.get("daemon", {}).get("min_profit_hot_alert_gp", 2000000)
    min_return_hot = settings.get("daemon", {}).get("min_return_hot_alert_pct", 0.08)
    
    from .notifications import send_hot_flip_alert

    for _, r in flips.iterrows():
        expected_profit = float(r["expected_profit_gp"])
        expected_return = float(r["expected_return_pct"])
        item_id = int(r["item_id"])
        
        rsi_val = float(r.get("rsi", 50.0))
        bb_l = float(r.get("bb_lower", 0.0))
        bb_u = float(r.get("bb_upper", 0.0))
        v_surge = float(r.get("vol_surge", 1.0))
        reason_str = f"RSI: {rsi_val:.1f} | BB: [{bb_l:,.0f} - {bb_u:,.0f}] | Vol: {v_surge:.1f}x"
        
        rec = Recommendation(
            strategy_name=r["strategy_name"],
            item_id=item_id,
            side="buy",
            qty=int(r["suggested_qty"]),
            price_each=int(r["buy_price"]),
            expected_profit_gp=expected_profit,
            expected_return_pct=expected_return,
            signal_type="pure_flip",
            reason=reason_str,
        )

        
        # Check if hot flip alert is warranted
        is_hot = (expected_profit >= min_profit_hot) or (expected_return >= min_return_hot)
        if is_hot:
            # Prevent duplicate alerts inside a rolling window
            anti_spam_hours = settings.get("daemon", {}).get("anti_spam_hours", 4)
            time_threshold = datetime.utcnow() - timedelta(hours=anti_spam_hours)

            already_alerted = (
                session.query(Recommendation)
                .filter(
                    Recommendation.item_id == item_id,
                    Recommendation.signal_type == "pure_flip",
                    Recommendation.created_at >= time_threshold,
                    ((Recommendation.expected_profit_gp >= min_profit_hot) | (Recommendation.expected_return_pct >= min_return_hot))
                )
                .first()
            ) is not None
            
            if not already_alerted:
                send_hot_flip_alert(
                    item_name=r["name"],
                    item_id=item_id,
                    buy_price=float(r["buy_price"]),
                    margin=float(r["margin_eff"]),
                    qty=int(r["suggested_qty"]),
                    profit=expected_profit,
                    return_pct=expected_return
                )
                
        session.add(rec)

    for _, r in processing.iterrows():
        rec = Recommendation(
            strategy_name=f"{r['required_skill'].lower()}_processing",
            item_id=None,
            side=None,
            qty=None,
            price_each=int(r["required_level"]),
            expected_profit_gp=float(r["profit_per_batch"])
            if "profit_per_batch" in r
            else None,
            expected_return_pct=None,
            signal_type="processing",
            reason=f"{r['recipe_name']} (Eligible: {r['eligible_accounts']})",
        )
        session.add(rec)

    session.commit()
    session.close()

    if send_digest:
        # Send HTML trade recommendations digest email
        from .notifications import send_trade_digest
        send_trade_digest()

