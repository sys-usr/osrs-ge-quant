# src/osrs_ge_quant/strategy.py
import math
from typing import Dict
import pandas as pd
import numpy as np

from .config import load_settings, load_strategies
from .db import get_session
from .models import Account, PricePoint

def calculate_osrs_tax(price: float) -> float:
    """
    Apply precise OSRS GE tax rule:
      - under 100 gp: tax free
      - 100+ gp: 1% tax, rounded down, capped at 5M gp per item
    """
    if price < 100:
        return 0.0
    tax = math.floor(price * 0.01)
    return min(tax, 5_000_000.0)

def load_accounts() -> Dict[str, Account]:
    session = get_session()
    accounts = {a.name: a for a in session.query(Account).filter_by(active=True).all()}
    session.close()
    return accounts

def effective_buy_limit(item_limit: int | None, active_accounts: int) -> int:
    if not item_limit:
        return 0
    return item_limit * active_accounts

def _get_strategy_cfg(name: str) -> dict:
    strategies = load_strategies()
    for s in strategies.get("strategies", []):
        if s.get("name") == name:
            return s
    # Fallback default if strategies.yaml is missing/empty
    return {"name": name, "params": {"min_daily_volume": 100, "min_margin_gp": 50, "max_spread_pct": 15.0}}

def compute_technical_indicators(df: pd.DataFrame, timestep: str) -> pd.DataFrame:
    """
    Compute Wilders RSI (14-period), Bollinger Bands (20-period),
    and Volume Surge for candidate items from their database timeseries history.
    """
    session = get_session()
    item_ids = df["item_id"].tolist() if "item_id" in df.columns else (df["id"].tolist() if "id" in df.columns else [])
    
    if not item_ids:
        session.close()
        # Ensure fallback columns exist
        df["rsi"] = 50.0
        df["bb_lower"] = df["avgLowPrice"] if "avgLowPrice" in df.columns else 0.0
        df["bb_upper"] = df["avgHighPrice"] if "avgHighPrice" in df.columns else 0.0
        df["vol_surge"] = 1.0
        return df

    # Fetch last 30 price points for the active timestep
    from sqlalchemy import select
    stmt = (
        select(PricePoint)
        .where(
            PricePoint.item_id.in_(item_ids),
            PricePoint.timestep == timestep
        )
        .order_by(PricePoint.item_id, PricePoint.ts.desc())
    )
    rows = session.execute(stmt).scalars().all()
    session.close()

    # Group rows by item_id
    from collections import defaultdict
    history = defaultdict(list)
    for r in rows:
        history[r.item_id].append(r)

    # Calculate indicators
    rsi_vals = {}
    bb_lower_vals = {}
    bb_upper_vals = {}
    vol_surge_vals = {}

    for item_id, p_list in history.items():
        # Sort chronologically
        p_list = sorted(p_list, key=lambda x: x.ts)
        if len(p_list) < 5:
            continue
        
        prices = []
        volumes = []
        for p in p_list:
            high = p.avg_high or 0.0
            low = p.avg_low or 0.0
            mid = (high + low) / 2.0 if high and low else (high or low or 0.0)
            prices.append(mid)
            volumes.append(p.high_vol or p.low_vol or 0.0)
            
        prices = np.array(prices)
        volumes = np.array(volumes)
        
        # 1. Bollinger Bands (20 periods or max available)
        n_bb = min(20, len(prices))
        recent_prices = prices[-n_bb:]
        ma = np.mean(recent_prices)
        std = np.std(recent_prices)
        bb_lower_vals[item_id] = ma - 2.0 * std if std > 0 else ma
        bb_upper_vals[item_id] = ma + 2.0 * std if std > 0 else ma
        
        # 2. Wilders RSI (14 periods or max available)
        n_rsi = min(14, len(prices) - 1)
        if n_rsi >= 5:
            deltas = np.diff(prices)
            seed = deltas[-n_rsi:]
            gains = seed[seed > 0]
            losses = -seed[seed < 0]
            avg_gain = np.mean(gains) if len(gains) > 0 else 0.0
            avg_loss = np.mean(losses) if len(losses) > 0 else 0.0
            if avg_loss == 0:
                rsi_vals[item_id] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi_vals[item_id] = 100.0 - (100.0 / (1.0 + rs))
        else:
            rsi_vals[item_id] = 50.0
            
        # 3. Volume Surge (last volume vs 10-period SMA)
        n_vol = min(10, len(volumes))
        if n_vol >= 3:
            current_vol = volumes[-1]
            avg_vol = np.mean(volumes[-n_vol:])
            vol_surge_vals[item_id] = current_vol / avg_vol if avg_vol > 0 else 1.0
        else:
            vol_surge_vals[item_id] = 1.0

    # Map back to the dataframe
    id_col = "item_id" if "item_id" in df.columns else "id"
    df["rsi"] = df[id_col].map(rsi_vals).fillna(50.0)
    df["bb_lower"] = df[id_col].map(bb_lower_vals).fillna(df["avgLowPrice"] if "avgLowPrice" in df.columns else 0.0)
    df["bb_upper"] = df[id_col].map(bb_upper_vals).fillna(df["avgHighPrice"] if "avgHighPrice" in df.columns else 0.0)
    df["vol_surge"] = df[id_col].map(vol_surge_vals).fillna(1.0)
    
    return df


def generate_flip_recommendations(df: pd.DataFrame, param_overrides: dict = None) -> pd.DataFrame:
    """
    Computes margin after GE tax and applies flip strategy rules.
    Accepts param_overrides so the backtester can dynamically sweep parameters.
    """
    settings = load_settings()
    strat_cfg = _get_strategy_cfg("high_margin_flip")
    tax = settings.get("ge", {}).get("tax_rate", 0.01)

    accounts = load_accounts()
    n_accounts = max(1, len(accounts)) # Prevent 0 multiplier if DB is empty

    work = df.copy()

    # Load analysis configs (blacklist & max price cap)
    analysis_cfg = settings.get("analysis", {})
    max_price = analysis_cfg.get("max_item_price_gp", 1500000000)
    blacklist = [item.lower() for item in analysis_cfg.get("blacklisted_items", [])]

    # Filter out blacklisted items and items priced above max_price
    work = work[work["avgLowPrice"] <= max_price]
    work = work[~work["name"].str.lower().isin(blacklist)]

    work["spread_gp"] = work["avgHighPrice"] - work["avgLowPrice"]

    work["spread_pct"] = work["spread_gp"] / work["avgLowPrice"].replace(0, pd.NA) * 100
    
    # Calculate effective margin: High price minus OSRS tax minus Low price
    def compute_margin(row):
        high = row["avgHighPrice"]
        low = row["avgLowPrice"]
        if pd.isna(high) or pd.isna(low) or high <= 0 or low <= 0:
            return 0.0
        return (high - calculate_osrs_tax(high)) - low

    work["margin_eff"] = work.apply(compute_margin, axis=1)

    # --- INJECT DYNAMIC PARAMS ---
    params = strat_cfg.get("params", {}).copy()
    if param_overrides:
        params.update(param_overrides)

    vol_col = "lowPriceVolume" if "lowPriceVolume" in work.columns else None

    # Apply filters
    timestep = settings.get("ge", {}).get("default_timestep", "24h")
    if vol_col and "min_daily_volume" in params:
        min_vol = params["min_daily_volume"]
        if timestep == "5m":
            min_vol = max(1.0, min_vol / 288.0)
        elif timestep == "1h":
            min_vol = max(1.0, min_vol / 24.0)
        elif timestep == "6h":
            min_vol = max(1.0, min_vol / 4.0)
        work = work[work[vol_col] >= min_vol]

    if "min_margin_gp" in params and "max_spread_pct" in params:
        work = work[
            (work["margin_eff"] >= params["min_margin_gp"])
            & (work["spread_pct"] <= params["max_spread_pct"])
        ]

    # Position sizing
    work["effective_limit"] = work["limit"].fillna(0).astype(int).apply(
        lambda L: effective_buy_limit(L, n_accounts)
    )

    if vol_col:
        if timestep == "5m":
            volume_4h = work[vol_col] * 48
        elif timestep == "1h":
            volume_4h = work[vol_col] * 4
        elif timestep == "6h":
            volume_4h = work[vol_col] * (4.0 / 6.0)
        else:
            volume_4h = work[vol_col] / 6.0
        work["vol_cap_qty"] = (volume_4h / 4.0).round().astype(int)
    else:
        work["vol_cap_qty"] = 1000

    work["suggested_qty"] = work[["effective_limit", "vol_cap_qty"]].min(axis=1).round().astype(int)
    work = work[work["suggested_qty"] > 0]

    work["expected_profit_gp"] = work["margin_eff"] * work["suggested_qty"]
    work["expected_return_pct"] = work["margin_eff"] / work["avgLowPrice"]
    work["strategy_name"] = strat_cfg["name"]
    work["buy_price"] = work["avgLowPrice"].round().astype(int)

    # Note: If the mean-reversion z-score logic is passed in via param_overrides,
    # we can apply it here. For a true stat-arb, we filter where z_score < -k_std
    if param_overrides and "z_scores" in param_overrides and "k_std" in params:
        work = work.merge(param_overrides["z_scores"], on="item_id", how="inner")
        work = work[work["z_score"] <= -params["k_std"]]

    if "item_id" not in work.columns and "id" in work.columns:
        work.rename(columns={"id": "item_id"}, inplace=True)
    elif "id" in work.columns:
        work = work.drop(columns=["id"])

    # Calculate and attach technical indicators for the final recommendations
    timestep = settings.get("ge", {}).get("default_timestep", "5m")
    work = compute_technical_indicators(work, timestep)
    
    cols = [
        "item_id", "name", "strategy_name", "avgHighPrice", 
        "avgLowPrice", "margin_eff", "suggested_qty", 
        "expected_profit_gp", "expected_return_pct", "limit", "buy_price",
        "rsi", "bb_lower", "bb_upper", "vol_surge"
    ]
    existing_cols = [c for c in cols if c in work.columns]
    
    return work[existing_cols].sort_values("expected_profit_gp", ascending=False)


def generate_processing_recommendations(df_24h: pd.DataFrame) -> pd.DataFrame:
    from .processing import evaluate_processing_opportunities
    from .hiscores import load_all_active_player_skills

    all_skills = load_all_active_player_skills()
    proc_df = evaluate_processing_opportunities(df_24h, all_skills)
    if not proc_df.empty:
        proc_df["strategy_name"] = proc_df["required_skill"].str.lower() + "_processing"
    return proc_df