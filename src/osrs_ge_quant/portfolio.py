from __future__ import annotations

from datetime import datetime
from typing import Dict, Any, List

import numpy as np
import pandas as pd
from sqlalchemy import func

from .db import get_session
from .models import Trade, PricePoint, Item, Account


def load_open_positions(as_of: datetime | None = None) -> pd.DataFrame:
    """
    Reconstruct open positions per (account, item) from the Trade table.

    Assumes:
        - side in {"buy", "sell"}
        - qty, price_each present

    Returns DataFrame:
        account_id, account_name, item_id, item_name, net_qty, avg_cost
    """
    session = get_session()
    q = session.query(Trade)
    if as_of is not None:
        q = q.filter(Trade.ts <= as_of)
    trades = q.all()

    if not trades:
        session.close()
        return pd.DataFrame()

    rows = []
    for t in trades:
        sign = 1 if t.side == "buy" else -1
        rows.append(
            {
                "account_id": t.account_id,
                "item_id": t.item_id,
                "qty": sign * t.qty,
                "notional": sign * t.qty * t.price_each,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        session.close()
        return df

    grouped = df.groupby(["account_id", "item_id"]).agg(
        net_qty=("qty", "sum"),
        net_notional=("notional", "sum"),
    )
    grouped = grouped[grouped["net_qty"] != 0]

    # attach names
    account_ids = grouped.index.get_level_values("account_id").unique().tolist()
    item_ids = grouped.index.get_level_values("item_id").unique().tolist()

    accounts = {
        a.id: a.name
        for a in session.query(Account.id, Account.name)
        .filter(Account.id.in_(account_ids))
        .all()
    }
    items = {
        i.id: i.name
        for i in session.query(Item.id, Item.name)
        .filter(Item.id.in_(item_ids))
        .all()
    }

    session.close()

    grouped = grouped.reset_index()
    grouped["account_name"] = grouped["account_id"].map(accounts)
    grouped["item_name"] = grouped["item_id"].map(items)
    grouped["avg_cost"] = grouped["net_notional"] / grouped["net_qty"]

    return grouped[
        [
            "account_id",
            "account_name",
            "item_id",
            "item_name",
            "net_qty",
            "avg_cost",
        ]
    ]


def mark_to_market(
    positions: pd.DataFrame,
    timestep: str = "24h",
) -> pd.DataFrame:
    """
    Mark open positions to latest price and compute PnL.

    Adds:
        mark_price, market_value, unrealized_pnl, pnl_pct
    """
    if positions.empty:
        return positions

    item_ids = positions["item_id"].unique().tolist()
    session = get_session()

    # latest ts per item for given timestep
    subq = (
        session.query(
            PricePoint.item_id,
            func.max(PricePoint.ts).label("max_ts"),
        )
        .filter(PricePoint.timestep == timestep, PricePoint.item_id.in_(item_ids))
        .group_by(PricePoint.item_id)
        .subquery()
    )

    rows = (
        session.query(
            PricePoint.item_id,
            PricePoint.avg_high,
            PricePoint.avg_low,
        )
        .join(
            subq,
            (PricePoint.item_id == subq.c.item_id)
            & (PricePoint.ts == subq.c.max_ts),
        )
        .all()
    )
    session.close()

    price_map = {}
    for r in rows:
        price_map[r[0]] = float(
            np.nanmean([r[1], r[2]]) if r[1] is not None or r[2] is not None else 0.0
        )

    positions = positions.copy()
    positions["mark_price"] = positions["item_id"].map(price_map).fillna(0.0)
    positions["market_value"] = positions["net_qty"] * positions["mark_price"]
    positions["unrealized_pnl"] = (
        positions["net_qty"] * (positions["mark_price"] - positions["avg_cost"])
    )
    positions["pnl_pct"] = positions["unrealized_pnl"] / (
        positions["net_qty"] * positions["avg_cost"]
    ).abs()

    return positions


def summarize_portfolio(positions_marked: pd.DataFrame) -> Dict[str, Any]:
    """
    Aggregate portfolio risk / PnL metrics from marked positions.
    """
    if positions_marked.empty:
        return {
            "total_market_value": 0.0,
            "total_unrealized_pnl": 0.0,
            "n_positions": 0,
        }

    total_mv = float(positions_marked["market_value"].sum())
    total_pnl = float(positions_marked["unrealized_pnl"].sum())
    n_pos = int((positions_marked["net_qty"] != 0).sum())

    by_account = (
        positions_marked.groupby("account_name")["market_value"].sum().to_dict()
    )

    top_contrib = (
        positions_marked.sort_values("unrealized_pnl", ascending=False)
        .head(10)[["item_id", "item_name", "unrealized_pnl"]]
        .to_dict("records")
    )

    return {
        "total_market_value": total_mv,
        "total_unrealized_pnl": total_pnl,
        "n_positions": n_pos,
        "by_account": by_account,
        "top_contributors": top_contrib,
    }
