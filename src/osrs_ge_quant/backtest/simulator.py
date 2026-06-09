# src/osrs_ge_quant/backtest/simulator.py

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import pandas as pd


@dataclass
class Position:
    qty: int = 0
    avg_price: float = 0.0  # volume-weighted average cost


@dataclass
class PortfolioState:
    ts: object
    cash: float
    equity: float
    positions_value: float
    positions: Dict[int, Position] = field(default_factory=dict)


class MarketSimulator:
    """
    Simple discrete-time backtest simulator.

    Assumptions:
      - All signals are market orders at given price (no slippage yet)
      - Unlimited liquidity at that price
      - We track:
          * cash
          * per-item positions (qty, avg cost)
          * equity curve over time
    """

    def __init__(self, initial_capital: float = 100_000_000):
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.positions: Dict[int, Position] = {}
        self.history: List[PortfolioState] = []

    def _update_position(self, item_id: int, side: str, qty: int, price: float):
        pos = self.positions.get(item_id, Position())

        if side == "buy":
            cost = qty * price
            if cost > self.cash:
                # Not enough cash: scale down
                qty = int(self.cash // price)
                cost = qty * price
            if qty <= 0:
                return

            new_qty = pos.qty + qty
            if new_qty > 0:
                new_avg = (pos.avg_price * pos.qty + cost) / new_qty
            else:
                new_avg = 0.0
            pos.qty = new_qty
            pos.avg_price = new_avg
            self.cash -= cost

        elif side == "sell":
            # Cannot sell more than we hold
            qty = min(qty, pos.qty)
            if qty <= 0:
                return
            proceeds = qty * price
            pos.qty -= qty
            self.cash += proceeds
            if pos.qty == 0:
                pos.avg_price = 0.0

        self.positions[item_id] = pos

    def step(
        self,
        ts,
        signals: pd.DataFrame,
        prices: pd.Series,
    ):
        """
        One time step:

        - signals: DataFrame with rows for this timestamp:
            columns: [item_id, side ("buy"/"sell"), qty, price]
        - prices: Series mapping item_id -> current mark price (for equity calc)
        """
        # Execute signals
        for _, s in signals.iterrows():
            item_id = int(s["item_id"])
            side = s["side"]
            qty = int(s["qty"])
            price = float(s["price"])
            self._update_position(item_id, side, qty, price)

        # Compute portfolio value
        positions_value = 0.0
        for item_id, pos in self.positions.items():
            mark_price = float(prices.get(item_id, pos.avg_price or 0.0))
            positions_value += pos.qty * mark_price

        equity = self.cash + positions_value

        state = PortfolioState(
            ts=ts,
            cash=self.cash,
            equity=equity,
            positions_value=positions_value,
            positions={k: Position(v.qty, v.avg_price) for k, v in self.positions.items()},
        )
        self.history.append(state)

    def run(
        self,
        timeline: pd.DatetimeIndex,
        signal_df: pd.DataFrame,
        price_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Main loop:

        - timeline: sorted list of timestamps
        - signal_df: all signals with columns [ts, item_id, side, qty, price]
        - price_df: full price history with columns [ts, item_id, avg_high, avg_low]

        For marking portfolio, we use avg_low as conservative exit price.
        """
        signal_df = signal_df.copy()
        signal_df.sort_values(["ts", "item_id"], inplace=True)

        price_df = price_df.copy()
        price_df.sort_values(["ts", "item_id"], inplace=True)

        for ts in timeline:
            ts_signals = signal_df[signal_df["ts"] == ts]
            ts_prices = price_df[price_df["ts"] == ts]

            # Build Series item_id -> mark_price
            price_series = ts_prices.set_index("item_id")["avg_low"]

            self.step(ts, ts_signals, price_series)

        # Convert history to DataFrame
        hist_rows = []
        for h in self.history:
            hist_rows.append(
                {
                    "ts": h.ts,
                    "cash": h.cash,
                    "equity": h.equity,
                    "positions_value": h.positions_value,
                }
            )

        return pd.DataFrame(hist_rows)
