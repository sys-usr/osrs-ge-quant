# src/osrs_ge_quant/webapp/backtest_tab.py

from __future__ import annotations

from typing import Any, Tuple

import pandas as pd
import plotly.graph_objs as go
from dash import html, dcc, Input, Output, State, callback, no_update

from ..backtest.engine import backtest_flip_strategy


def build_backtest_tab():
    return dcc.Tab(
        label="Backtest",
        value="tab-backtest",
        children=[
            html.Div(
                [
                    html.H3("Mean-Reversion Flip Backtest"),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("Years of history"),
                                    dcc.Input(
                                        id="bt-years",
                                        type="number",
                                        value=3,
                                        min=1,
                                        step=1,
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                            html.Div(
                                [
                                    html.Label("Timestep"),
                                    dcc.Dropdown(
                                        id="bt-timestep",
                                        options=[
                                            {"label": "1d_weirdgloop (daily, long history)", "value": "1d_weirdgloop"},
                                            {"label": "24h snapshot", "value": "24h"},
                                        ],
                                        value="1d_weirdgloop",
                                        clearable=False,
                                        style={"width": "260px"},
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                            html.Div(
                                [
                                    html.Label("Initial capital (gp)"),
                                    dcc.Input(
                                        id="bt-initial-capital",
                                        type="number",
                                        value=100_000_000,
                                        min=1_000_000,
                                        step=1_000_000,
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                        ],
                        style={"display": "flex", "flex-wrap": "wrap", "margin-bottom": "1rem"},
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Label("k_std (entry/exit threshold)"),
                                    dcc.Input(
                                        id="bt-k-std",
                                        type="number",
                                        value=1.0,
                                        step=0.1,
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                            html.Div(
                                [
                                    html.Label("Position fraction (of initial capital)"),
                                    dcc.Input(
                                        id="bt-position-fraction",
                                        type="number",
                                        value=0.05,
                                        step=0.01,
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                            html.Div(
                                [
                                    html.Label("Fee rate"),
                                    dcc.Input(
                                        id="bt-fee-rate",
                                        type="number",
                                        value=0.01,
                                        step=0.001,
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                            html.Div(
                                [
                                    html.Label("Top N items by liquidity"),
                                    dcc.Input(
                                        id="bt-top-n",
                                        type="number",
                                        value=300,
                                        min=10,
                                        step=10,
                                    ),
                                ],
                                style={"margin-right": "1rem"},
                            ),
                        ],
                        style={"display": "flex", "flex-wrap": "wrap", "margin-bottom": "1rem"},
                    ),
                    html.Button(
                        "Run backtest",
                        id="bt-run",
                        n_clicks=0,
                        style={"margin-bottom": "1rem"},
                    ),
                    html.Div(id="bt-summary", style={"whiteSpace": "pre-wrap", "fontFamily": "monospace"}),
                    dcc.Graph(
                        id="bt-equity-graph",
                        figure=go.Figure(),
                        style={"height": "500px", "margin-top": "1rem"},
                    ),
                ],
                style={"padding": "1rem"},
            )
        ],
    )


@callback(
    Output("bt-summary", "children"),
    Output("bt-equity-graph", "figure"),
    Input("bt-run", "n_clicks"),
    State("bt-years", "value"),
    State("bt-timestep", "value"),
    State("bt-initial-capital", "value"),
    State("bt-k-std", "value"),
    State("bt-position-fraction", "value"),
    State("bt-fee-rate", "value"),
    State("bt-top-n", "value"),
)
def run_backtest_callback(
    n_clicks: int,
    years: int,
    timestep: str,
    initial_capital: int,
    k_std: float,
    position_fraction: float,
    fee_rate: float,
    top_n: int,
) -> Tuple[Any, go.Figure]:
    if not n_clicks:
        return "", go.Figure()

    res = backtest_flip_strategy(
        years=int(years or 3),
        timestep=timestep or "1d_weirdgloop",
        initial_capital=int(initial_capital or 100_000_000),
        k_std=float(k_std or 1.0),
        position_fraction=float(position_fraction or 0.05),
        fee_rate=float(fee_rate or 0.01),
        top_n=int(top_n or 300),
    )

    if "error" in res:
        return f"[ERROR] {res['error']}", go.Figure()

    metrics = res["metrics"]
    cfg = res["config"]
    equity_curve = res.get("equity_curve", [])

    # Build equity curve figure
    fig = go.Figure()
    if equity_curve:
        eq_df = pd.DataFrame(equity_curve)
        fig.add_trace(
            go.Scatter(
                x=eq_df["ts"],
                y=eq_df["equity"],
                mode="lines",
                name="Equity",
            )
        )
        fig.update_layout(
            title="Equity Curve",
            xaxis_title="Time",
            yaxis_title="GP",
        )

    summary = (
        f"Universe: top {cfg.get('top_n')} items | "
        f"Timestep: {cfg.get('timestep')} | "
        f"Fee: {cfg.get('fee_rate') * 100:.2f}%\n"
        f"Initial capital: {cfg.get('initial_capital'):,.0f} gp\n\n"
        f"Final equity:   {metrics.get('final_equity', 0):,.0f} gp\n"
        f"Total return:   {metrics.get('total_return', 0) * 100:.2f}%\n"
        f"Sharpe ratio:   {metrics.get('sharpe', 0):.2f}\n"
        f"Max drawdown:   {metrics.get('max_drawdown', 0) * 100:.2f}%\n"
    )

    return summary, fig
