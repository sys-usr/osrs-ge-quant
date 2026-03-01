# src/osrs_ge_quant/backtest/metrics.py

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_returns(equity_curve: pd.DataFrame) -> pd.Series:
    """
    Compute simple returns from an equity curve DataFrame with 'equity' column.
    """
    eq = equity_curve["equity"].astype(float)
    return eq.pct_change().fillna(0.0)


def total_return(equity_curve: pd.DataFrame) -> float:
    eq = equity_curve["equity"].astype(float)
    if eq.empty:
        return 0.0
    return float(eq.iloc[-1] / eq.iloc[0] - 1.0)


def max_drawdown(equity_curve: pd.DataFrame) -> float:
    eq = equity_curve["equity"].astype(float)
    if eq.empty:
        return 0.0
    roll_max = eq.cummax()
    dd = (eq - roll_max) / roll_max
    return float(dd.min())


def sharpe_ratio(equity_curve: pd.DataFrame, periods_per_year: int = 365 * 24) -> float:
    """
    Simple Sharpe: mean(returns) / std(returns) * sqrt(periods_per_year)
    """
    rets = compute_returns(equity_curve)
    if rets.std() == 0:
        return 0.0
    return float(rets.mean() / rets.std() * np.sqrt(periods_per_year))
