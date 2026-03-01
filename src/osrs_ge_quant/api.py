from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, Query
from sqlalchemy import text

from .db import DB_PATH, engine
from .export import build_terminal_snapshot

app = FastAPI(title="OSRS GE Quant API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "db_path": str(DB_PATH),
        "api": "osrs-ge-quant",
        "version": "0.1.0",
    }


@app.get("/api/snapshot")
def snapshot(top_items: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return build_terminal_snapshot(top_items=top_items)


@app.get("/api/items/{item_id}/history")
def item_history(
    item_id: int,
    timestep: str = Query("24h"),
    limit: int = Query(500, ge=10, le=5000),
) -> dict[str, Any]:
    query = text(
        """
        SELECT
            p.item_id,
            i.name AS item_name,
            p.ts,
            p.timestep,
            p.avg_high,
            p.avg_low,
            p.high_vol,
            p.low_vol
        FROM prices p
        JOIN items i ON i.id = p.item_id
        WHERE p.item_id = :item_id
          AND p.timestep = :timestep
        ORDER BY p.ts DESC
        LIMIT :limit
        """
    )
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(query, {"item_id": item_id, "timestep": timestep, "limit": limit})]

    return {
        "item_id": item_id,
        "timestep": timestep,
        "rows": list(reversed(rows)),
    }


@app.get("/api/recommendations")
def recommendations(
    status: Literal["all", "open", "taken", "skipped"] = Query("all"),
    limit: int = Query(200, ge=10, le=2000),
) -> dict[str, Any]:
    base = """
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
    """
    filters = {
        "all": "",
        "open": "WHERE taken_trade_id IS NULL AND COALESCE(skipped, 0) = 0",
        "taken": "WHERE taken_trade_id IS NOT NULL",
        "skipped": "WHERE COALESCE(skipped, 0) = 1",
    }
    query = text(f"{base} {filters[status]} ORDER BY created_at DESC LIMIT :limit")
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(query, {"limit": limit})]

    return {"status": status, "count": len(rows), "rows": rows}


def create_app() -> FastAPI:
    return app
