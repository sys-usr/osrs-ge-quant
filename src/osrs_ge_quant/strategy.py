# src/osrs_ge_quant/strategy.py
import math
from typing import Dict

import pandas as pd

from .config import load_settings, load_strategies
from .db import get_session
from .models import Account


def load_accounts() -> Dict[str, Account]:
    session = get_session()
    return {a.name: a for a in session.query(Account).filter_by(active=True).all()}


def effective_buy_limit(item_limit: int | None, active_accounts: int) -> int:
    if not item_limit:
        return 0
    return item_limit * active_accounts


def _get_strategy_cfg(name: str) -> dict:
    strategies = load_strategies()
    return next(s for s in strategies if s["name"] == name)


def generate_flip_recommendations(df: pd.DataFrame) -> pd.DataFrame:
    """
    Input df: one row per item with avgHighPrice/avgLowPrice/etc.
    We compute margin after GE tax and apply flip strategy rules.
    """
    settings = load_settings()
    strat_cfg = _get_strategy_cfg("high_margin_flip")
    tax = settings["ge"]["tax_rate"]

    accounts = load_accounts()
    n_accounts = len(accounts)

    work = df.copy()
    # spread and margin
    work["spread_gp"] = work["avgLowPrice"] - work["avgHighPrice"]
    work["spread_pct"] = work["spread_gp"] / work["avgHighPrice"].replace(0, pd.NA) * 100
    work["margin_eff"] = work["avgLowPrice"] * (1 - tax) - work["avgHighPrice"]

    params = strat_cfg["params"]
    vol_col = "lowPriceVolume" if "lowPriceVolume" in work.columns else None

    if vol_col:
        work = work[work[vol_col] >= params["min_daily_volume"]]

    work = work[
        (work["margin_eff"] >= params["min_margin_gp"])
        & (work["spread_pct"] <= params["max_spread_pct"])
    ]

    # effective buy limit across all active accounts
    active_accounts = n_accounts
    work["effective_limit"] = work["limit"].fillna(0).astype(int).apply(
        lambda L: effective_buy_limit(L, active_accounts)
    )

    # volume cap
    if vol_col:
        work["vol_cap_qty"] = (work[vol_col] / 4).astype(int)
    else:
        work["vol_cap_qty"] = 1000

    work["suggested_qty"] = work[["effective_limit", "vol_cap_qty"]].min(axis=1)
    work = work[work["suggested_qty"] > 0]

    work["expected_profit_gp"] = work["margin_eff"] * work["suggested_qty"]
    work["expected_return_pct"] = work["margin_eff"] / work["avgHighPrice"]
    work["strategy_name"] = strat_cfg["name"]
    work["buy_price"] = work["avgHighPrice"].round().astype(int)

    work.rename(columns={"id": "item_id"}, inplace=True)
    cols = [
        "item_id",
        "name",
        "strategy_name",
        "avgHighPrice",
        "avgLowPrice",
        "margin_eff",
        "suggested_qty",
        "expected_profit_gp",
        "expected_return_pct",
        "limit",
        "buy_price",
    ]
    work = work[cols].sort_values("expected_profit_gp", ascending=False)
    return work


def generate_processing_recommendations(df_24h: pd.DataFrame) -> pd.DataFrame:
    """
    Uses processing recipes and player skills to evaluate profitable skilling conversions.
    """
    from .processing import evaluate_processing_opportunities  # avoid circular
    from .hiscores import load_player_skills

    skills = load_player_skills()
    proc_df = evaluate_processing_opportunities(df_24h, skills)
    # You can add max_batches, gp/hr etc here later
    proc_df["strategy_name"] = "herblore_processing"
    return proc_df
