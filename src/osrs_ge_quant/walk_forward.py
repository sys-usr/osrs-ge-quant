# src/osrs_ge_quant/walk_forward.py
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from typing import Dict, Any, List

from .backtest import backtest_flip_strategy
from .bayesian_opt import BayesianOptimizer

def walk_forward_backtest(
    total_days: int = 60,
    train_days: int = 21,
    test_days: int = 7,
    timestep: str = "1h",
    initial_capital: float = 100_000_000.0,
) -> Dict[str, Any]:
    """
    Executes walk-forward backtesting splits:
    - Train on a rolling window of length `train_days`.
    - Optimize parameters (k_std and min_margin_gp) using Bayesian Optimization.
    - Test out-of-sample on the following `test_days`.
    - Returns final metrics and out-of-sample equity curve.
    """
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=total_days)
    
    current_train_start = start_date
    equity = initial_capital
    equity_curve = [equity]
    
    splits = []
    
    # We bounds k_std in [0.5, 2.5] and min_margin_gp in [10.0, 5000.0]
    bounds = [(0.5, 2.5), (10.0, 5000.0)]
    
    while current_train_start + timedelta(days=train_days + test_days) <= end_date:
        train_end = current_train_start + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)
        
        # 1. Define objective for Bayesian Optimization on training window
        def obj_func(k_std, min_margin_gp):
            res = backtest_flip_strategy(
                timestep=timestep,
                initial_capital=equity,
                k_std=float(k_std),
                fee_rate=0.01,
                start_date=current_train_start,
                end_date=train_end
            )
            metrics = res.get("metrics", {})
            # Maximize Sharpe ratio; fall back to total return if Sharpe is zero
            score = metrics.get("sharpe", 0.0)
            if score == 0.0 or np.isnan(score):
                score = metrics.get("total_return", 0.0)
            return float(score) if not np.isnan(score) else 0.0
            
        # 2. Run Bayesian Optimization on Training Window
        print(f"[Walk-Forward] Optimizing parameters for train window: {current_train_start.strftime('%m-%d')} to {train_end.strftime('%m-%d')}")
        optimizer = BayesianOptimizer(objective=obj_func, bounds=bounds, n_init=3, xi=0.01)
        try:
            best_params, best_score = optimizer.run_optimization(n_iter=6)
            opt_k_std, opt_min_margin = best_params
        except Exception as e:
            print(f"  Optimization error: {e}. Using default parameters.")
            opt_k_std, opt_min_margin = 1.0, 100.0
            
        print(f"  Optimized parameters: k_std={opt_k_std:.2f}, min_margin_gp={opt_min_margin:.0f}")
        
        # 3. Test on Out-of-Sample Window
        test_res = backtest_flip_strategy(
            timestep=timestep,
            initial_capital=equity,
            k_std=float(opt_k_std),
            fee_rate=0.01,
            start_date=train_end,
            end_date=test_end
        )
        
        test_metrics = test_res.get("metrics", {})
        final_eq = test_metrics.get("final_equity", equity)
        
        splits.append({
            "train_start": current_train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": train_end.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
            "opt_k_std": float(opt_k_std),
            "opt_min_margin": float(opt_min_margin),
            "oos_return": float((final_eq - equity) / equity) if equity else 0.0
        })
        
        equity = final_eq
        equity_curve.append(equity)
        
        # Walk forward by test_days
        current_train_start += timedelta(days=test_days)
        
    final_return = (equity - initial_capital) / initial_capital if initial_capital else 0.0
    
    return {
        "final_equity": equity,
        "total_return": final_return,
        "splits": splits,
        "equity_curve": equity_curve
    }
