# src/osrs_ge_quant/backtest/__init__.py

from __future__ import annotations

from typing import Dict, Any
from .engine import backtest_flip_strategy
from .sweep import sweep_backtests
from .engine import backtest_flip_strategy as _backtest_impl

__all__ = [
    "backtest_flip_strategy",
    "sweep_backtests",
]

def backtest_flip_strategy(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    initial_capital: int = 100_000_000,
    k_std: float = 1.0,
    position_fraction: float = 0.05,
    fee_rate: float = 0.01,
    top_n: int = 300,
) -> Dict[str, Any]:
    """
    Public API for the flip backtest. This keeps
    `from osrs_ge_quant.backtest import backtest_flip_strategy`
    working and just delegates to engine.backtest_flip_strategy.
    """
    return _backtest_impl(
        years=years,
        timestep=timestep,
        initial_capital=initial_capital,
        k_std=k_std,
        position_fraction=position_fraction,
        fee_rate=fee_rate,
        top_n=top_n,
    )


def run_flip_backtest(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    initial_capital: int = 100_000_000,
    k_std: float = 1.0,
    position_fraction: float = 0.05,
    fee_rate: float = 0.01,
    top_n: int = 300,
) -> Dict[str, Any]:
    """
    Backwards-compatible alias for older code that imported:
      `from osrs_ge_quant.backtest import run_flip_backtest`
    """
    return _backtest_impl(
        years=years,
        timestep=timestep,
        initial_capital=initial_capital,
        k_std=k_std,
        position_fraction=position_fraction,
        fee_rate=fee_rate,
        top_n=top_n,
    )
