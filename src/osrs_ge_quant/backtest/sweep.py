# src/osrs_ge_quant/backtest/sweep.py

from __future__ import annotations

from typing import List, Dict, Any

import pandas as pd

from .engine import backtest_flip_strategy


def sweep_backtests(
    years: int,
    timestep: str,
    initial_capital: int,
    fee_rate: float,
    top_n: int,
    k_std_grid: List[float],
    position_grid: List[float],
) -> List[Dict[str, Any]]:
    """
    Grid search over (k_std, position_fraction) for the flip backtest.

    Returns a list of dicts with metrics + config fields, e.g.:

    {
        "years": ...,
        "timestep": ...,
        "initial_capital": ...,
        "fee_rate": ...,
        "top_n": ...,
        "k_std": ...,
        "position_fraction": ...,
        "total_return": ...,
        "sharpe": ...,
        "max_drawdown": ...,
        "final_equity": ...,
    }
    """
    results: List[Dict[str, Any]] = []

    print(
        f"[SWEEP] years={years} timestep={timestep} "
        f"initial_capital={initial_capital} fee={fee_rate} top_n={top_n}"
    )
    print(f"[SWEEP] k_std grid: {k_std_grid}")
    print(f"[SWEEP] position_fraction grid: {position_grid}")
    print("-" * 72)

    for k in k_std_grid:
        for pos in position_grid:
            print(
                f"[SWEEP] running backtest: k_std={k:.3f} "
                f"position_fraction={pos:.3f} ..."
            )

            res = backtest_flip_strategy(
                years=years,
                timestep=timestep,
                initial_capital=initial_capital,
                k_std=k,
                position_fraction=pos,
                fee_rate=fee_rate,
                top_n=top_n,
            )

            metrics = res.get("metrics", {})
            cfg = res.get("config", {})

            total_ret = float(metrics.get("total_return", 0.0))
            sharpe = float(metrics.get("sharpe", 0.0))
            max_dd = float(metrics.get("max_drawdown", 0.0))
            final_eq = float(metrics.get("final_equity", 0.0))

            print(
                f"  -> ret={total_ret*100:7.2f}% sharpe={sharpe:5.2f} "
                f"maxDD={max_dd*100:7.2f}% final_eq={final_eq:,.0f}"
            )

            row: Dict[str, Any] = dict(cfg)
            row.update(
                {
                    "k_std": k,
                    "position_fraction": pos,
                    "total_return": total_ret,
                    "sharpe": sharpe,
                    "max_drawdown": max_dd,
                    "final_equity": final_eq,
                }
            )
            results.append(row)

    print("-" * 72)
    print(f"[SWEEP] {len(results)} combos evaluated.")

    return results


def results_to_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """Helper to convert sweep results into a pandas DataFrame."""
    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results)
