from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import func

from ..db import get_session
from ..models import PricePoint, Item


# ------------------------------
# Data loading
# ------------------------------

def load_price_history(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    start_date: datetime = None,
    end_date: datetime = None,
) -> pd.DataFrame:
    """
    Load price history for all items at the given timestep and
    return a tidy DataFrame with price + volume.

    Columns:
        item_id, ts, price, high_vol, low_vol, avg_high, avg_low
    """
    session = get_session()

    if start_date is not None and end_date is not None:
        start_ts = start_date
        end_ts = end_date
    else:
        max_ts = (
            session.query(func.max(PricePoint.ts))
            .filter(PricePoint.timestep == timestep)
            .scalar()
        )

        if max_ts is None:
            session.close()
            return pd.DataFrame()

        start_ts = max_ts - timedelta(days=365 * years)
        end_ts = max_ts

    rows = (
        session.query(
            PricePoint.item_id,
            PricePoint.ts,
            PricePoint.avg_high,
            PricePoint.avg_low,
            PricePoint.high_vol,
            PricePoint.low_vol,
        )
        .filter(
            PricePoint.timestep == timestep,
            PricePoint.ts >= start_ts,
            PricePoint.ts <= end_ts,
        )
        .all()
    )

    session.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [
            {
                "item_id": r[0],
                "ts": r[1],
                "avg_high": r[2],
                "avg_low": r[3],
                "high_vol": r[4],
                "low_vol": r[5],
            }
            for r in rows
        ]
    )

    df["price"] = df[["avg_high", "avg_low"]].mean(axis=1)
    df = df.dropna(subset=["price"])
    return df


# ------------------------------
# Strategy config
# ------------------------------

@dataclass
class BacktestConfig:
    """
    Core config for the mean-reversion flip strategy.
    """
    years: int = 3
    timestep: str = "1d_weirdgloop"

    initial_capital: int = 100_000_000
    k_std: float = 1.0
    position_fraction: float = 0.05

    rolling_window: int = 50
    min_history: int = 100
    top_n: int = 300          # liquid universe size
    fee_rate: float = 0.01    # 1% fee / tax assumption

    # Safety / diagnostics
    max_days: int | None = None  # optionally restrict number of days to simulate


# ------------------------------
# Signal computation
# ------------------------------

def _compute_signals(
    prices: pd.DataFrame,
    cfg: BacktestConfig,
) -> pd.DataFrame:
    """
    Compute rolling z-score per item based on rolling mean/std.
    """
    roll_mean = prices.rolling(
        cfg.rolling_window,
        min_periods=cfg.rolling_window,
    ).mean()
    roll_std = prices.rolling(
        cfg.rolling_window,
        min_periods=cfg.rolling_window,
    ).std()

    z = (prices - roll_mean) / roll_std
    return z


# ------------------------------
# Core backtest
# ------------------------------

def backtest_flip_strategy(
    years: int = 3,
    timestep: str = "1d_weirdgloop",
    initial_capital: int = 100_000_000,
    k_std: float = 1.0,
    position_fraction: float = 0.05,
    fee_rate: float = 0.01,
    top_n: int = 300,
    start_date: datetime = None,
    end_date: datetime = None,
) -> Dict[str, Any]:
    """
    Main mean-reversion flip backtest.

    - Universe: items with enough history, limited to top_n by liquidity.
    - Buy: z <= -k_std, no existing position.
    - Sell: z >= +k_std or price NaN.
    - Size: initial_capital * position_fraction per full position.
    - Fees: symmetric fee_rate on notional for buys & sells.

    Returns:
        dict with:
            config, metrics, equity_curve_head, equity_curve_tail,
            per_item_stats, trades_sample
    """

    cfg = BacktestConfig(
        years=years,
        timestep=timestep,
        initial_capital=initial_capital,
        k_std=k_std,
        position_fraction=position_fraction,
        fee_rate=fee_rate,
        top_n=top_n,
    )

    print(
        "[BT] starting backtest... "
        f"years={cfg.years} timestep={cfg.timestep} "
        f"initial_capital={cfg.initial_capital} "
        f"k_std={cfg.k_std} position_fraction={cfg.position_fraction} "
        f"fee_rate={cfg.fee_rate} top_n={cfg.top_n}"
    )

    df = load_price_history(years=cfg.years, timestep=cfg.timestep, start_date=start_date, end_date=end_date)
    if df.empty:
        print("[BT] no price history, abort.")
        return {"error": "No price history available.", "config": asdict(cfg)}

    # Filter items by minimum history
    counts = df.groupby("item_id")["ts"].count()
    eligible_items = counts[counts >= cfg.min_history].index
    df = df[df["item_id"].isin(eligible_items)]

    if df.empty:
        print("[BT] no items with sufficient history.")
        return {"error": "No items with sufficient history.", "config": asdict(cfg)}

    # Liquidity filter using median high_vol
    vol_by_item = (
        df.groupby("item_id")["high_vol"]
        .median()
        .fillna(0)
        .sort_values(ascending=False)
    )
    liquid_items = set(vol_by_item.head(cfg.top_n).index)
    df = df[df["item_id"].isin(liquid_items)]

    prices = df.pivot(index="ts", columns="item_id", values="price").sort_index()
    prices = prices.dropna(how="all")
    if prices.empty:
        print("[BT] empty price matrix after cleaning.")
        return {"error": "No usable price matrix.", "config": asdict(cfg)}

    if cfg.max_days is not None and len(prices) > cfg.max_days:
        prices = prices.iloc[-cfg.max_days :]

    z = _compute_signals(prices, cfg)

    # Simulation state
    dates = list(prices.index)
    items = list(prices.columns)

    cash: float = float(cfg.initial_capital)
    positions: Dict[int, float] = {int(i): 0.0 for i in items}
    equity_curve: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []

    FEE_RATE = cfg.fee_rate

    def portfolio_value(px_row: pd.Series) -> float:
        val = 0.0
        for i, qty in positions.items():
            if qty <= 0:
                continue
            p = px_row.get(i)
            if p is None or np.isnan(p):
                continue
            val += qty * float(p)
        return val

    def record_trade(
        ts,
        item_id: int,
        side: str,
        qty: float,
        price: float,
        fee: float,
        equity_before: float,
        equity_after: float,
    ):
        trades.append(
            {
                "ts": ts,
                "item_id": int(item_id),
                "side": side,
                "qty": float(qty),
                "price": float(price),
                "fee": float(fee),
                "equity_before": float(equity_before),
                "equity_after": float(equity_after),
            }
        )

    def buy(item_id: int, px: float, ts) -> None:
        nonlocal cash
        if px <= 0:
            return
        target_value = cfg.initial_capital * cfg.position_fraction
        qty = target_value // px
        if qty <= 0:
            return
        cost = qty * px
        fee = cost * FEE_RATE
        total = cost + fee
        if cash < total:
            return
        equity_before = cash + portfolio_value(prices.loc[ts])
        cash -= total
        positions[item_id] = positions.get(item_id, 0.0) + qty
        equity_after = cash + portfolio_value(prices.loc[ts])
        record_trade(ts, item_id, "buy", qty, px, fee, equity_before, equity_after)

    def sell(item_id: int, px: float, ts) -> None:
        nonlocal cash
        qty = positions.get(item_id, 0.0)
        if qty <= 0 or px <= 0:
            return
        gross = qty * px
        fee = gross * FEE_RATE
        net = gross - fee
        equity_before = cash + portfolio_value(prices.loc[ts])
        cash += net
        positions[item_id] = 0.0
        equity_after = cash + portfolio_value(prices.loc[ts])
        record_trade(ts, item_id, "sell", qty, px, fee, equity_before, equity_after)

    # Simulation loop
    for ts in dates:
        px_today = prices.loc[ts]
        z_today = z.loc[ts]

        positions_val = portfolio_value(px_today)
        equity = cash + positions_val

        if not np.isfinite(equity):
            print("[BT] equity blew up / NaN, breaking.")
            break

        equity_curve.append(
            {
                "ts": ts,
                "cash": float(cash),
                "positions_value": float(positions_val),
                "equity": float(equity),
            }
        )

        if equity <= 0:
            print("[BT] equity <= 0, stopping.")
            break

        # 1) Sells
        for item_id in items:
            iid = int(item_id)
            if positions.get(iid, 0.0) <= 0:
                continue
            zi = z_today.get(item_id)
            pi = px_today.get(item_id)
            # sell if z >= +k or price invalid
            sell_signal = (
                zi is not None
                and not np.isnan(zi)
                and zi >= cfg.k_std
            ) or (pi is None or np.isnan(pi))

            if not sell_signal:
                continue

            if pi is None or np.isnan(pi) or pi <= 0:
                continue

            sell(iid, float(pi), ts)

        # 2) Buys
        # we don't rescale by current equity here (fixed risk per trade)
        for item_id in items:
            iid = int(item_id)
            if positions.get(iid, 0.0) > 0:
                continue
            zi = z_today.get(item_id)
            pi = px_today.get(item_id)
            if zi is None or np.isnan(zi) or zi > -cfg.k_std:
                continue
            if pi is None or np.isnan(pi) or pi <= 0:
                continue
            buy(iid, float(pi), ts)
            if cash <= 0:
                break

    print("[BT] backtest finished.")

    # ------------------------------
    # Metrics
    # ------------------------------
    eq_df = pd.DataFrame(equity_curve).sort_values("ts")
    if eq_df.empty:
        return {"error": "No equity data", "config": asdict(cfg)}

    eq_series = eq_df["equity"].astype(float)
    start_eq = float(eq_series.iloc[0])
    end_eq = float(eq_series.iloc[-1])
    total_ret = (end_eq / start_eq) - 1.0

    rets = eq_series.pct_change().dropna()
    if len(rets) > 1 and rets.std() > 0:
        sharpe = float(np.sqrt(252) * rets.mean() / rets.std())
    else:
        sharpe = 0.0

    roll_max = eq_series.cummax()
    dd = (eq_series - roll_max) / roll_max
    max_dd = float(dd.min()) if len(dd) else 0.0

    # Per-item stats from trade log
    trades_df = pd.DataFrame(trades)
    per_item_stats: List[Dict[str, Any]] = []

    if not trades_df.empty:
        # approximate PnL per item by matching buys/sells FIFO
        def compute_item_pnl(trades_item: pd.DataFrame) -> Tuple[float, int]:
            trades_item = trades_item.sort_values("ts")
            # simple implementation: assume full round-trip per sell
            pnl = 0.0
            buys_cost = 0.0
            pos_qty = 0.0
            trades_count = len(trades_item)

            for _, t in trades_item.iterrows():
                if t["side"] == "buy":
                    buys_cost += t["qty"] * t["price"] + t["fee"]
                    pos_qty += t["qty"]
                else:
                    # sell
                    proceeds = t["qty"] * t["price"] - t["fee"]
                    # allocate proportionally to full position
                    if pos_qty > 0:
                        avg_cost_per_unit = buys_cost / pos_qty
                        cost_for_sold = avg_cost_per_unit * t["qty"]
                        pnl += proceeds - cost_for_sold
                        pos_qty -= t["qty"]
                        buys_cost -= cost_for_sold
            return float(pnl), int(trades_count)

        grouped = trades_df.groupby("item_id")
        session = get_session()
        names = {
            r.id: r.name
            for r in session.query(Item.id, Item.name)
            .filter(Item.id.in_(grouped.groups.keys()))
            .all()
        }
        session.close()

        for item_id, g in grouped:
            pnl, n_trades = compute_item_pnl(g)
            per_item_stats.append(
                {
                    "item_id": int(item_id),
                    "name": names.get(item_id),
                    "pnl": pnl,
                    "n_trades": n_trades,
                }
            )

        per_item_stats.sort(key=lambda x: x["pnl"], reverse=True)

    result = {
        "config": asdict(cfg),
        "metrics": {
            "total_return": float(total_ret),
            "max_drawdown": float(max_dd),
            "sharpe": float(sharpe),
            "final_equity": float(end_eq),
        },
        "equity_curve": eq_df.to_dict("records"),
        "equity_curve_head": eq_df.head(10).to_dict("records"),
        "equity_curve_tail": eq_df.tail(10).to_dict("records"),
        "per_item_stats": per_item_stats,
        "trades_sample": trades_df.head(50).to_dict("records")
        if not trades_df.empty
        else [],
    }

    return result
