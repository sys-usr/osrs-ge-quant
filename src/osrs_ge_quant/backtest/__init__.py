# src/osrs_ge_quant/backtest/__init__.py

from __future__ import annotations

from typing import List, Dict, Any

from .engine import backtest_flip_strategy
from .sweep import sweep_backtests as _sweep_backtests


def sweep_backtests(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    initial_capital: int = 100_000_000,
    k_std_values: List[float] = None,
    position_fractions: List[float] = None,
    fee_rate: float = 0.01,
    top_n: int = 300,
) -> List[Dict[str, Any]]:
    """
    Compatibility wrapper for CLI sweep command.
    Delegates to the matrix-based grid search backtester in sweep.py.
    """
    if k_std_values is None:
        k_std_values = [1.0]
    if position_fractions is None:
        position_fractions = [0.05]

    return _sweep_backtests(
        years=years,
        timestep=timestep,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        top_n=top_n,
        k_std_grid=k_std_values,
        position_grid=position_fractions,
    )