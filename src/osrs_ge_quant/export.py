from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from .db import engine


def _to_records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    cleaned = df.where(pd.notnull(df), None)
    return cleaned.to_dict(orient="records")


def export_terminal_snapshot(output_path: Path, top_items: int = 100) -> Path:
    """
    Export a JSON snapshot optimized for JS dashboards.
    """
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    accounts = pd.read_sql(
        text(
            """
            SELECT id, name, rsn, starting_gp, active, role
            FROM accounts
            ORDER BY name
            """
        ),
        engine,
    )

    pnl = pd.read_sql(
        text(
            """
            SELECT
                a.name AS account_name,
                COALESCE(SUM(r.expected_profit_gp), 0) AS approx_pnl_gp
            FROM accounts a
            LEFT JOIN trades t ON t.account_id = a.id
            LEFT JOIN recommendations r ON r.taken_trade_id = t.id
            GROUP BY a.name
            ORDER BY approx_pnl_gp DESC
            """
        ),
        engine,
    )

    recs = pd.read_sql(
        text(
            """
            SELECT
                id,
                created_at,
                strategy_name,
                item_id,
                side,
                qty,
                price_each,
                expected_profit_gp,
                expected_return_pct,
                signal_type,
                reason,
                taken_trade_id,
                skipped
            FROM recommendations
            ORDER BY created_at DESC
            LIMIT 500
            """
        ),
        engine,
    )

    prices = pd.read_sql(
        text(
            """
            WITH ranked AS (
                SELECT
                    p.item_id,
                    i.name AS item_name,
                    p.ts,
                    p.timestep,
                    p.avg_high,
                    p.avg_low,
                    p.high_vol,
                    p.low_vol,
                    ROW_NUMBER() OVER (PARTITION BY p.item_id, p.timestep ORDER BY p.ts DESC) AS rn
                FROM prices p
                JOIN items i ON i.id = p.item_id
                WHERE p.timestep = '24h'
            )
            SELECT
                item_id,
                item_name,
                ts,
                timestep,
                avg_high,
                avg_low,
                high_vol,
                low_vol,
                (COALESCE(avg_high, 0) + COALESCE(avg_low, 0)) / 2.0 AS mid_price,
                (COALESCE(high_vol, 0) + COALESCE(low_vol, 0)) AS total_volume
            FROM ranked
            WHERE rn = 1
            ORDER BY total_volume DESC
            LIMIT :top_items
            """
        ),
        engine,
        params={"top_items": top_items},
    )

    payload = {
        "meta": {
            "top_items": top_items,
            "generator": "osrs-ge-quant export-terminal-snapshot",
        },
        "accounts": _to_records(accounts),
        "account_pnl": _to_records(pnl),
        "recommendations": _to_records(recs),
        "liquid_items": _to_records(prices),
    }

    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output_path
