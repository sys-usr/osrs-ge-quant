# src/osrs_ge_quant/backtest.py
from datetime import datetime, timedelta
from typing import Dict, Any, List
import numpy as np
import pandas as pd
from sqlalchemy import select

from .db import get_session
from .models import PricePoint, Item
from .strategy import generate_flip_recommendations
from .config import load_settings

def _parse_timestep(timestep: str) -> timedelta:
    if timestep == "5m": return timedelta(minutes=5)
    elif timestep == "1h": return timedelta(hours=1)
    elif timestep == "6h": return timedelta(hours=6)
    elif timestep == "24h" or timestep.startswith("1d"): return timedelta(days=1)
    return timedelta(hours=1)

def load_history_window(start: datetime, end: datetime, timestep: str = "1h") -> pd.DataFrame:
    session = get_session()
    stmt = (
        select(PricePoint, Item)
        .join(Item, PricePoint.item_id == Item.id)
        .where(PricePoint.ts >= start, PricePoint.ts < end, PricePoint.timestep == timestep)
    )
    rows = session.execute(stmt).all()
    session.close()
    
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

def backtest_flip_strategy(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    initial_capital: int = 100_000_000,
    k_std: float = 1.0, 
    position_fraction: float = 0.05,
    fee_rate: float = 0.01,
    top_n: int = 300,
) -> Dict[str, Any]:
    
    end = datetime.utcnow()
    start = end - timedelta(days=365*years)
    step_delta = _parse_timestep(timestep)

    current = start
    capital = initial_capital
    equity_curve = []
    
    overrides = {
        "k_std": k_std, 
        "min_margin_gp": 10,
    }

    while current < end:
        window_end = current + step_delta
        df = load_history_window(current, window_end, timestep)
        
        if df.empty:
            current = window_end
            equity_curve.append(capital)
            continue
            
        snap = df.sort_values("ts").groupby("item_id").tail(1)
        recs = generate_flip_recommendations(snap, param_overrides=overrides)
        
        for _, r in recs.head(top_n).iterrows():
            qty = min(r["suggested_qty"], 1000)
            buy = r["avgHighPrice"] * qty
            
            if buy > capital * position_fraction:
                continue
                
            sell = r["avgLowPrice"] * qty * (1 - fee_rate)
            capital += (sell - buy)

        equity_curve.append(capital)
        current = window_end

    eq_series = pd.Series(equity_curve)
    returns = eq_series.pct_change().dropna()
    
    total_return = (capital - initial_capital) / initial_capital if initial_capital else 0.0
    
    periods_per_year = 365 if step_delta.days >= 1 else (365 * 24)
    sharpe = 0.0
    if len(returns) > 0 and returns.std() != 0:
        sharpe = (returns.mean() / returns.std()) * np.sqrt(periods_per_year)

    rolling_max = eq_series.cummax()
    drawdowns = (eq_series - rolling_max) / rolling_max
    max_drawdown = drawdowns.min() if len(drawdowns) > 0 else 0.0

    return {
        "config": {"top_n": top_n, "timestep": timestep, "fee_rate": fee_rate, "initial_capital": initial_capital},
        "metrics": {
            "final_equity": capital,
            "total_return": total_return,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown
        },
        "per_item_stats": [] 
    }

def sweep_backtests(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    initial_capital: int = 100_000_000,
    k_std_values: List[float] = None,
    position_fractions: List[float] = None,
    fee_rate: float = 0.01,
    top_n: int = 300,
) -> List[Dict[str, Any]]:
    
    if k_std_values is None: k_std_values = [1.0]
    if position_fractions is None: position_fractions = [0.05]

    results = []
    for k in k_std_values:
        for pf in position_fractions:
            print(f"[Sweep] Running k_std={k:.2f}, pos_frac={pf:.3f}...")
            res = backtest_flip_strategy(
                years=years, timestep=timestep, initial_capital=initial_capital,
                k_std=k, position_fraction=pf, fee_rate=fee_rate, top_n=top_n
            )
            m = res.get("metrics", {})
            results.append({
                "k_std": k, "position_fraction": pf,
                "total_return": m.get("total_return", 0.0),
                "sharpe": m.get("sharpe", 0.0),
                "max_drawdown": m.get("max_drawdown", 0.0)
            })
    return results