# bt_dashboard.py
#
# OSRS GE Quant – Full GUI:
#   - Param sweeps + auto-plan
#   - Save sweep as CSV (for analysis / sharing)
#   - Sector labels on sweeps + sector filter in visuals
#   - Screeners + quick flip suggestions (size trades given capital)
#   - Portfolio view
#   - Trade logging
#   - Event study
#
# This version also fixes layout issues by pinning graph heights, and
# uses suppress_callback_exceptions=True for dynamic tabs.
#
# Usage:
#   (osrs_ge_quant) python bt_dashboard.py sweeps_3y_weirdgloop.csv
#   (osrs_ge_quant) python bt_dashboard.py  # start empty
#

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Dash, dcc, html, Input, Output, State, no_update

# Quant imports
from osrs_ge_quant.backtest.sweep import sweep_backtests
from osrs_ge_quant.screeners import run_zscore_screener
from osrs_ge_quant.portfolio import load_open_positions
from osrs_ge_quant.event_study import run_event_study
from osrs_ge_quant.db import get_session
from osrs_ge_quant.models import Account, Trade
from osrs_ge_quant.config import data_dir


# ------------------------------
# Helpers
# ------------------------------
def _empty_fig(title: str = "No data yet"):
    fig = go.Figure()
    fig.update_layout(
        title=title,
        xaxis_title="",
        yaxis_title="",
        margin=dict(l=40, r=40, t=40, b=40),
    )
    return fig


def _df_from_records(records: Optional[List[Dict[str, Any]]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _auto_plan_from_df(
    df: pd.DataFrame,
    min_sharpe: Optional[float],
    max_dd: Optional[float],
    min_return: Optional[float],
) -> Optional[Dict[str, Any]]:
    """Pick best row under constraints. Returns dict with row + shortlist, or None."""
    required = {
        "k_std",
        "position_fraction",
        "total_return",
        "sharpe",
        "max_drawdown",
        "final_equity",
    }
    if df.empty or not required.issubset(df.columns):
        return None

    sel = df.copy()

    if min_return is not None:
        sel = sel[sel["total_return"] >= min_return]
    if min_sharpe is not None:
        sel = sel[sel["sharpe"] >= min_sharpe]
    if max_dd is not None:
        # max_drawdown is negative; "less negative" is better, so want >= threshold
        sel = sel[sel["max_drawdown"] >= max_dd]

    if sel.empty:
        sel = df.copy()

    sel = sel.sort_values(
        by=["sharpe", "total_return", "position_fraction"],
        ascending=[False, False, True],
    )
    best = sel.iloc[0]

    return {
        "best": best,
        "candidates": sel.head(5),
    }


def log_trade_to_db(
    account_name: str,
    side: str,
    item_id: int,
    item_name: str,
    qty: int,
    price_each: int,
    note: str,
) -> None:
    """Insert a trade row matching the CLI's log-trade behavior."""
    session = get_session()
    try:
        acct = (
            session.query(Account)
            .filter(Account.name == account_name)
            .one_or_none()
        )
        if acct is None:
            raise ValueError(f"Account not found: {account_name!r}")

        t = Trade(
            ts=datetime.utcnow(),
            account_id=acct.id,
            item_id=item_id,
            item_name=item_name,
            side=side,
            qty=qty,
            price_each=price_each,
            note=note or "",
        )
        session.add(t)
        session.commit()
    finally:
        session.close()


def _saves_dir() -> Path:
    d = data_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _risk_bucket(z: float, price: float) -> str:
    """
    Crude risk signal:
      - Huge |z| + low price -> 'Aggressive'
      - Moderate |z| -> 'Normal'
      - Small |z|   -> 'Conservative'
    """
    if z is None:
        return "Unknown"
    az = abs(z)
    if az >= 3.0 and price <= 10_000:
        return "Aggressive"
    if az >= 2.0:
        return "Normal"
    return "Conservative"


# ------------------------------
# Build Dash app
# ------------------------------
def build_app(initial_df: pd.DataFrame) -> Dash:
    # NOTE: suppress_callback_exceptions=True fixes "ID not found in layout"
    app = Dash(__name__, suppress_callback_exceptions=True)
    app.title = "OSRS GE Quant – Terminal"

    # Preload sweep-store from CSV if provided
    initial_records = (
        initial_df.to_dict("records")
        if (initial_df is not None and not initial_df.empty)
        else []
    )

    app.layout = html.Div(
        style={"fontFamily": "system-ui, -apple-system, BlinkMacSystemFont, sans-serif",
               "margin": "16px"},
        children=[
            html.Div(
                style={"display": "flex", "alignItems": "center", "justifyContent": "space-between"},
                children=[
                    html.Div(
                        children=[
                            html.H2("OSRS GE Quant",
                                    style={"marginBottom": "0px"}),
                            html.Div(
                                "Short-horizon flip terminal – sweeps, screeners, portfolio, trades.",
                                style={"fontSize": "12px", "color": "#666"},
                            ),
                        ]
                    ),
                    html.Div(
                        style={"textAlign": "right", "fontSize": "12px", "color": "#888"},
                        children=[
                            html.Div("Mode: aggressive short-term flipping"),
                            html.Div("Data: GE timeseries (1d_weirdgloop / 24h)"),
                        ],
                    ),
                ],
            ),

            # Stores for sweep & screener data
            dcc.Store(id="sweep-store", data={"records": initial_records}),
            dcc.Store(id="screener-store", data={"records": []}),

            dcc.Tabs(
                id="main-tabs",
                value="tab-sweep",
                children=[
                    dcc.Tab(label="Sweep & Auto-plan", value="tab-sweep"),
                    dcc.Tab(label="Visuals (Heatmap / Sharpe)", value="tab-visuals"),
                    dcc.Tab(label="Screeners & Flips", value="tab-screeners"),
                    dcc.Tab(label="Portfolio", value="tab-portfolio"),
                    dcc.Tab(label="Trades", value="tab-trades"),
                    dcc.Tab(label="Event Study", value="tab-event"),
                ],
                style={"marginTop": "12px"},
            ),
            html.Div(id="tab-content"),
        ],
    )

    # --------------------------
    # Tab content router
    # --------------------------
    @app.callback(Output("tab-content", "children"), Input("main-tabs", "value"))
    def render_tab(tab_value: str):
        if tab_value == "tab-sweep":
            return _layout_sweep_tab()
        if tab_value == "tab-visuals":
            return _layout_visuals_tab()
        if tab_value == "tab-screeners":
            return _layout_screeners_tab()
        if tab_value == "tab-portfolio":
            return _layout_portfolio_tab()
        if tab_value == "tab-trades":
            return _layout_trades_tab()
        if tab_value == "tab-event":
            return _layout_event_tab()
        return html.Div("Unknown tab")

    # --------------------------
    # Sweep + auto-plan + save
    # --------------------------
    @app.callback(
        Output("sweep-store", "data"),
        Output("sweep-status", "children"),
        Input("btn-run-sweep", "n_clicks"),
        State("sweep-store", "data"),
        State("input-years", "value"),
        State("input-timestep", "value"),
        State("input-init-cap", "value"),
        State("input-fee-rate", "value"),
        State("input-top-n", "value"),
        State("input-k-grid", "value"),
        State("input-pos-grid", "value"),
        State("input-sector-label", "value"),
        prevent_initial_call=True,
    )
    def run_sweep(
        n_clicks,
        existing_store,
        years,
        timestep,
        init_cap,
        fee_rate,
        top_n,
        k_grid_str,
        pos_grid_str,
        sector_label,
    ):
        if not n_clicks:
            return no_update, no_update

        try:
            k_list = [float(x.strip()) for x in k_grid_str.split(",") if x.strip()]
            pos_list = [float(x.strip()) for x in pos_grid_str.split(",") if x.strip()]
        except Exception as e:
            return (
                existing_store,
                f"[ERROR] Bad grid input: {e}",
            )

        if not k_list or not pos_list:
            return existing_store, "[ERROR] k_std grid and position grid cannot be empty."

        label = (sector_label or "").strip() or "default"

        print(
            f"[SWEEP] years={years} timestep={timestep} "
            f"initial_capital={init_cap} fee={fee_rate} top_n={top_n} sector={label}"
        )
        print(f"[SWEEP] k_std grid: {k_list}")
        print(f"[SWEEP] position_fraction grid: {pos_list}")

        results = sweep_backtests(
            years=years,
            timestep=timestep,
            initial_capital=int(init_cap),
            fee_rate=float(fee_rate),
            top_n=int(top_n),
            k_std_grid=k_list,
            position_grid=pos_list,
        )

        df_new = pd.DataFrame(results)
        df_new["sector"] = label

        existing_df = _df_from_records(existing_store.get("records") if existing_store else [])
        if not existing_df.empty:
            df_all = pd.concat([existing_df, df_new], ignore_index=True)
        else:
            df_all = df_new

        msg = f"[SWEEP] Done. {len(df_new)} combos evaluated for sector '{label}'. Total stored rows: {len(df_all)}"

        return {"records": df_all.to_dict("records")}, msg

    @app.callback(
        Output("auto-plan-output", "children"),
        Input("btn-auto-plan", "n_clicks"),
        State("sweep-store", "data"),
        State("ap-min-sharpe", "value"),
        State("ap-max-dd", "value"),
        State("ap-min-return", "value"),
        State("ap-sector-filter", "value"),
        prevent_initial_call=True,
    )
    def run_auto_plan(
        n_clicks,
        sweep_data,
        min_sharpe,
        max_dd,
        min_return,
        sector_filter,
    ):
        if not n_clicks:
            return no_update

        df = _df_from_records(
            sweep_data.get("records") if sweep_data else []
        )
        if df.empty:
            return "[AUTO] No sweep data yet. Run a sweep first."

        if sector_filter and sector_filter != "__ALL__":
            df = df[df.get("sector", "") == sector_filter]
            if df.empty:
                return f"[AUTO] No rows for sector '{sector_filter}'."

        result = _auto_plan_from_df(df, min_sharpe, max_dd, min_return)
        if result is None:
            return "[AUTO] Sweep data missing required columns."

        best = result["best"]
        candidates = result["candidates"]

        years = int(best.get("years", 3))
        timestep = str(best.get("timestep", "1d_weirdgloop"))
        init_cap = int(best.get("initial_capital", 100_000_000))
        fee_rate = float(best.get("fee_rate", 0.01))
        top_n = int(best.get("top_n", 300))
        sector = best.get("sector", "default")

        cmd = (
            "osrs-ge-quant backtest "
            f"--years {years} "
            f"--timestep {timestep} "
            f"--initial-capital {init_cap} "
            f"--k-std {best['k_std']} "
            f"--position-fraction {best['position_fraction']} "
            f"--fee-rate {fee_rate} "
            f"--top-n {top_n}"
        )

        lines = []
        lines.append(f"[AUTO] Recommended configuration (sector: {sector}):")
        lines.append("-" * 40)
        lines.append(f"k_std:             {best['k_std']}")
        lines.append(f"position_fraction: {best['position_fraction']}")
        lines.append(f"total_return:      {best['total_return']*100:.2f}%")
        lines.append(f"sharpe:            {best['sharpe']:.2f}")
        lines.append(f"max_drawdown:      {best['max_drawdown']*100:.2f}%")
        lines.append(f"final_equity:      {best['final_equity']:,.0f} gp")
        lines.append("")
        lines.append("[AUTO] Equivalent CLI command:")
        lines.append(cmd)
        lines.append("")
        lines.append("[AUTO] Top 5 candidates:")
        for _, r in candidates.head(5).iterrows():
            lines.append(
                f"k={r['k_std']:.2f} pos={r['position_fraction']:.3f}  "
                f"ret={r['total_return']*100:7.2f}%  "
                f"sharpe={r['sharpe']:5.2f}  "
                f"dd={r['max_drawdown']*100:7.2f}%  "
                f"eq={r['final_equity']:,.0f}  "
                f"sector={r.get('sector','')}"
            )

        return html.Pre("\n".join(lines), style={"whiteSpace": "pre-wrap"})

    @app.callback(
        Output("save-sweep-status", "children"),
        Input("btn-save-sweep", "n_clicks"),
        State("sweep-store", "data"),
        State("save-filename", "value"),
        prevent_initial_call=True,
    )
    def save_sweep_csv(n_clicks, sweep_data, filename):
        if not n_clicks:
            return no_update

        df = _df_from_records(
            sweep_data.get("records") if sweep_data else []
        )
        if df.empty:
            return "[SAVE] No sweep data to save."

        name = (filename or "").strip() or "sweep_latest.csv"
        if not name.lower().endswith(".csv"):
            name += ".csv"

        out_dir = _saves_dir()
        out_path = out_dir / name
        df.to_csv(out_path, index=False)

        return f"[SAVE] Wrote {len(df)} rows to {out_path}"

    # --------------------------
    # Visuals callbacks
    # --------------------------
    @app.callback(
        Output("sector-dropdown", "options"),
        Output("sector-dropdown", "value"),
        Input("sweep-store", "data"),
    )
    def update_sector_dropdown(sweep_data):
        df = _df_from_records(
            sweep_data.get("records") if sweep_data else []
        )
        if df.empty or "sector" not in df.columns:
            opts = [{"label": "All sectors", "value": "__ALL__"}]
            return opts, "__ALL__"

        sectors = sorted(set(str(s) for s in df["sector"].dropna().unique()))
        opts = [{"label": "All sectors", "value": "__ALL__"}] + [
            {"label": s, "value": s} for s in sectors
        ]
        return opts, "__ALL__"

    @app.callback(
        Output("heatmap", "figure"),
        Output("sharpe-surface", "figure"),
        Output("results-table", "figure"),
        Input("metric-dropdown", "value"),
        Input("sector-dropdown", "value"),
        Input("sweep-store", "data"),
    )
    def update_visuals(metric: str, sector_value: str, sweep_data):
        df = _df_from_records(
            sweep_data.get("records") if sweep_data else []
        )
        if df.empty:
            msg = "No sweep data yet"
            return _empty_fig(msg), _empty_fig(msg), _empty_fig(msg)

        if sector_value and sector_value != "__ALL__":
            df = df[df.get("sector", "") == sector_value]
            if df.empty:
                msg = f"No rows for sector '{sector_value}'"
                return _empty_fig(msg), _empty_fig(msg), _empty_fig(msg)

        if "k_std" not in df.columns or "position_fraction" not in df.columns:
            msg = "Sweep missing k_std/position_fraction"
            return _empty_fig(msg), _empty_fig(msg), _empty_fig(msg)

        if metric not in df.columns:
            msg = f"Metric '{metric}' not found"
            return _empty_fig(msg), _empty_fig(msg), _empty_fig(msg)

        # Heatmap
        pivot = df.pivot_table(
            index="k_std",
            columns="position_fraction",
            values=metric,
            aggfunc="mean",
        )
        pivot = pivot.sort_index().sort_index(axis=1)

        heat = px.imshow(
            pivot,
            aspect="auto",
            origin="lower",
            labels=dict(
                x="position_fraction",
                y="k_std",
                color=metric,
            ),
        )
        heat.update_layout(
            margin=dict(l=40, r=20, t=40, b=40),
            title=f"{metric} heatmap (sector: {sector_value or 'All'})",
        )

        # Sharpe surface
        if "sharpe" in df.columns:
            spivot = df.pivot_table(
                index="k_std",
                columns="position_fraction",
                values="sharpe",
                aggfunc="mean",
            )
            spivot = spivot.sort_index().sort_index(axis=1)
            ks = spivot.index.to_list()
            pos = spivot.columns.to_list()
            Z = spivot.values

            surface = go.Figure(
                data=[
                    go.Surface(
                        x=pos,
                        y=ks,
                        z=Z,
                    )
                ]
            )
            surface.update_layout(
                scene=dict(
                    xaxis_title="position_fraction",
                    yaxis_title="k_std",
                    zaxis_title="sharpe",
                ),
                margin=dict(l=40, r=40, t=40, b=40),
                title=f"Sharpe surface (sector: {sector_value or 'All'})",
            )
        else:
            surface = _empty_fig("No 'sharpe' column in sweep")

        # Results table (top 30 by metric)
        table_df = df.copy()
        asc = False
        if metric == "max_drawdown":
            # less negative (higher) is better, so descending
            asc = False

        table_df = table_df.sort_values(metric, ascending=asc)

        cols = [
            c
            for c in [
                "sector",
                "k_std",
                "position_fraction",
                "total_return",
                "sharpe",
                "max_drawdown",
                "final_equity",
                "years",
                "timestep",
                "fee_rate",
                "top_n",
            ]
            if c in table_df.columns
        ]
        table_df = table_df[cols].head(30)

        header_vals = list(table_df.columns)
        cell_vals = [table_df[c].tolist() for c in header_vals]

        table_fig = go.Figure(
            data=[
                go.Table(
                    header=dict(values=header_vals, fill_color="lightgrey", align="left"),
                    cells=dict(values=cell_vals, align="left"),
                )
            ]
        )
        table_fig.update_layout(margin=dict(l=0, r=0, t=30, b=0))

        return heat, surface, table_fig

    # --------------------------
    # Screener + quick flips
    # --------------------------
    @app.callback(
        Output("screener-store", "data"),
        Output("screener-status", "children"),
        Input("btn-run-screener", "n_clicks"),
        State("scr-years", "value"),
        State("scr-timestep", "value"),
        State("scr-z-cutoff", "value"),
        State("scr-limit", "value"),
        prevent_initial_call=True,
    )
    def run_screener(
        n_clicks,
        years,
        timestep,
        z_cutoff,
        limit,
    ):
        if not n_clicks:
            return no_update, no_update

        print(
            f"[SCREEN] years={years} timestep={timestep} "
            f"z_cutoff={z_cutoff} limit={limit}"
        )
        
        try:
            years_val = int(round(float(years or 1)))
        except Exception:
            years_val = 1
        if years_val < 1:
            years_val = 1

        df = run_zscore_screener(
            years=years_val,
            timestep=str(timestep),
            z_cutoff=float(z_cutoff),
            limit=int(limit),
        )

        if df is None or df.empty:
            return {"records": []}, "[SCREEN] No rows returned."

        return {"records": df.to_dict("records")}, f"[SCREEN] {len(df)} rows returned."

    @app.callback(
        Output("screener-table", "figure"),
        Input("screener-store", "data"),
    )
    def update_screener_table(scr_data):
        df = _df_from_records(
            scr_data.get("records") if scr_data else []
        )
        if df.empty:
            return _empty_fig("No screener data yet")

        cols = list(df.columns)
        ordered: List[str] = []
        for c in ["item_id", "item_name", "price", "z_score"]:
            if c in cols:
                ordered.append(c)
        for c in cols:
            if c not in ordered:
                ordered.append(c)
        df = df[ordered]

        header_vals = list(df.columns)
        cell_vals = [df[c].tolist() for c in header_vals]

        table_fig = go.Figure(
            data=[
                go.Table(
                    header=dict(values=header_vals, fill_color="lightgrey", align="left"),
                    cells=dict(values=cell_vals, align="left"),
                )
            ]
        )
        table_fig.update_layout(margin=dict(l=0, r=0, t=30, b=0))
        return table_fig

    @app.callback(
        Output("quick-flips-table", "figure"),
        Output("quick-flips-status", "children"),
        Input("btn-run-quick-flips", "n_clicks"),
        State("screener-store", "data"),
        State("qf-total-capital", "value"),
        State("qf-position-fraction", "value"),
        State("qf-max-positions", "value"),
        prevent_initial_call=True,
    )
    def run_quick_flips(
        n_clicks,
        scr_data,
        total_cap,
        pos_frac,
        max_positions,
    ):
        if not n_clicks:
            return _empty_fig("Run screener first"), no_update

        df = _df_from_records(
            scr_data.get("records") if scr_data else []
        )
        if df.empty:
            return _empty_fig("No screener results – run screener first"), "[QF] No screener data."

        total_cap = float(total_cap or 0)
        pos_frac = float(pos_frac or 0.05)
        max_positions = int(max_positions or 10)
        if total_cap <= 0:
            return _empty_fig("Set a positive capital amount"), "[QF] Capital must be > 0."

        # We want items with the most negative z_score (cheap vs history).
        if "z_score" not in df.columns or "price" not in df.columns:
            return _empty_fig("Screener missing price/z_score"), "[QF] Screener missing columns."

        df = df.sort_values("z_score")  # most negative first
        df = df.head(max_positions)

        qf_rows = []
        for _, row in df.iterrows():
            price = float(row["price"])
            z = float(row["z_score"])
            if price <= 0:
                continue
            capital_per_pos = total_cap * pos_frac
            qty = int(capital_per_pos // price)
            if qty <= 0:
                continue

            risk = _risk_bucket(z, price)

            qf_rows.append(
                {
                    "item_id": row["item_id"],
                    "item_name": row.get("item_name", ""),
                    "price": price,
                    "z_score": z,
                    "qty_suggested": qty,
                    "notional_gp": qty * price,
                    "risk": risk,
                }
            )

        if not qf_rows:
            return _empty_fig("No viable positions at this sizing"), "[QF] No viable positions."

        qf_df = pd.DataFrame(qf_rows)

        header_vals = list(qf_df.columns)
        cell_vals = [qf_df[c].tolist() for c in header_vals]

        fig = go.Figure(
            data=[
                go.Table(
                    header=dict(values=header_vals, fill_color="lightgrey", align="left"),
                    cells=dict(values=cell_vals, align="left"),
                )
            ]
        )
        fig.update_layout(margin=dict(l=0, r=0, t=30, b=0))

        text = (
            "[QF] Suggested flips based on z-score screener.\n"
            "- qty_suggested = floor(total_capital * position_fraction / price)\n"
            "- risk = heuristic based on |z_score| and price\n"
            "This is short-horizon mean-reversion: you’re buying statistically cheap items "
            "with size scaled to your capital."
        )

        return fig, text

    # --------------------------
    # Portfolio callbacks
    # --------------------------
    @app.callback(
        Output("portfolio-table", "figure"),
        Output("portfolio-status", "children"),
        Input("btn-refresh-portfolio", "n_clicks"),
        prevent_initial_call=True,
    )
    def refresh_portfolio(n_clicks):
        if not n_clicks:
            return _empty_fig("No portfolio data yet"), no_update

        try:
            df = load_open_positions()
        except Exception as e:
            return _empty_fig("Error loading portfolio"), f"[PORTFOLIO] Error: {e}"

        if df is None or df.empty:
            return _empty_fig("No open positions"), "[PORTFOLIO] No open positions found."

        header_vals = list(df.columns)
        cell_vals = [df[c].tolist() for c in header_vals]

        fig = go.Figure(
            data=[
                go.Table(
                    header=dict(values=header_vals, fill_color="lightgrey", align="left"),
                    cells=dict(values=cell_vals, align="left"),
                )
            ]
        )
        fig.update_layout(margin=dict(l=0, r=0, t=30, b=0))

        return fig, f"[PORTFOLIO] {len(df)} open rows."

    # --------------------------
    # Trades callbacks
    # --------------------------
    @app.callback(
        Output("trade-status", "children"),
        Input("btn-submit-trade", "n_clicks"),
        State("trade-account", "value"),
        State("trade-side", "value"),
        State("trade-item-id", "value"),
        State("trade-item-name", "value"),
        State("trade-qty", "value"),
        State("trade-price-each", "value"),
        State("trade-note", "value"),
        prevent_initial_call=True,
    )
    def submit_trade(
        n_clicks,
        account,
        side,
        item_id,
        item_name,
        qty,
        price_each,
        note,
    ):
        if not n_clicks:
            return no_update

        try:
            log_trade_to_db(
                account_name=str(account).strip(),
                side=str(side),
                item_id=int(item_id),
                item_name=str(item_name).strip(),
                qty=int(qty),
                price_each=int(price_each),
                note=str(note or ""),
            )
        except Exception as e:
            return f"[TRADES] Error: {e}"

        return "[TRADES] Trade logged successfully. Refresh portfolio to see changes."

    # --------------------------
    # Event study callbacks
    # --------------------------
    @app.callback(
        Output("event-status", "children"),
        Output("event-graph", "figure"),
        Output("event-table", "figure"),
        Input("btn-run-event-study", "n_clicks"),
        State("event-date", "date"),
        State("event-pre-days", "value"),
        State("event-post-days", "value"),
        State("event-timestep", "value"),
        prevent_initial_call=True,
    )
    def run_event_callback(
        n_clicks,
        date_str,
        pre_days,
        post_days,
        timestep,
    ):
        if not n_clicks:
            return no_update, _empty_fig("No event study yet"), _empty_fig(
                "No event study yet"
            )

        if not date_str:
            return (
                "[EVENT] Please choose an event date.",
                _empty_fig("No event date"),
                _empty_fig("No event date"),
            )

        try:
            event_ts = datetime.fromisoformat(date_str)
            pre_days = int(pre_days)
            post_days = int(post_days)
        except Exception as e:
            return (
                f"[EVENT] Bad inputs: {e}",
                _empty_fig("Bad inputs"),
                _empty_fig("Bad inputs"),
            )

        df = run_event_study(
            event_ts=event_ts,
            pre_days=pre_days,
            post_days=post_days,
            timestep=str(timestep),
        )

        if df is None or df.empty:
            return (
                "[EVENT] No price data for that window/timestep.",
                _empty_fig("No data"),
                _empty_fig("No data"),
            )

        # Line graph: avg_return over offset
        fig_line = go.Figure()
        fig_line.add_trace(
            go.Scatter(
                x=df["offset"],
                y=df["avg_return"],
                mode="lines+markers",
                name="avg_return",
            )
        )
        if "median_return" in df.columns:
            fig_line.add_trace(
                go.Scatter(
                    x=df["offset"],
                    y=df["median_return"],
                    mode="lines+markers",
                    name="median_return",
                )
            )
        fig_line.update_layout(
            title="Event Study – avg/median return vs days offset",
            xaxis_title="Days relative to event",
            yaxis_title="Relative return",
            margin=dict(l=40, r=40, t=40, b=40),
        )

        # Table
        header_vals = list(df.columns)
        cell_vals = [df[c].tolist() for c in header_vals]

        fig_table = go.Figure(
            data=[
                go.Table(
                    header=dict(values=header_vals, fill_color="lightgrey", align="left"),
                    cells=dict(values=cell_vals, align="left"),
                )
            ]
        )
        fig_table.update_layout(margin=dict(l=0, r=0, t=30, b=0))

        msg = (
            f"[EVENT] Computed event study for window "
            f"{-pre_days}..+{post_days} days around {event_ts.date()} "
            f"({len(df)} offsets)."
        )

        return msg, fig_line, fig_table

    return app


# ------------------------------
# Tab layouts (static HTML)
# ------------------------------
def _card(children, title=None):
    return html.Div(
        style={
            "backgroundColor": "#fafafa",
            "border": "1px solid #ddd",
            "borderRadius": "6px",
            "padding": "12px 16px",
            "marginBottom": "12px",
            "boxShadow": "0 1px 2px rgba(0,0,0,0.05)",
        },
        children=[
            html.H4(title, style={"marginTop": "0"}) if title else None,
            *(children if isinstance(children, list) else [children]),
        ],
    )


def _layout_sweep_tab():
    return html.Div(
        style={"marginTop": "16px"},
        children=[
            html.Div(
                style={"display": "flex", "gap": "24px"},
                children=[
                    html.Div(
                        style={"flex": "1"},
                        children=[
                            _card(
                                title="Run parameter sweep (short-horizon flip strategy)",
                                children=[
                                    html.Div(
                                        [
                                            html.Label("Sector label (for grouping sweeps)"),
                                            dcc.Input(
                                                id="input-sector-label",
                                                type="text",
                                                value="All",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Years of history (short horizon → 0.5–1)"),
                                            dcc.Input(
                                                id="input-years",
                                                type="number",
                                                value=1,
                                                min=0.25,
                                                step=0.25,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Timestep"),
                                            dcc.Input(
                                                id="input-timestep",
                                                type="text",
                                                value="1d_weirdgloop",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Initial capital (gp)"),
                                            dcc.Input(
                                                id="input-init-cap",
                                                type="number",
                                                value=100_000_000,
                                                min=1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Fee / tax rate (0.01 = 1%)"),
                                            dcc.Input(
                                                id="input-fee-rate",
                                                type="number",
                                                value=0.01,
                                                step=0.001,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Top N liquid items"),
                                            dcc.Input(
                                                id="input-top-n",
                                                type="number",
                                                value=300,
                                                min=10,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("k_std grid (comma separated)"),
                                            dcc.Input(
                                                id="input-k-grid",
                                                type="text",
                                                value="0.8,1.0,1.2",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("position_fraction grid (comma separated)"),
                                            dcc.Input(
                                                id="input-pos-grid",
                                                type="text",
                                                value="0.03,0.05,0.08",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Button(
                                        "Run sweep",
                                        id="btn-run-sweep",
                                        n_clicks=0,
                                        style={"marginTop": "12px"},
                                    ),
                                    html.Div(
                                        id="sweep-status",
                                        style={"marginTop": "12px", "fontFamily": "monospace", "fontSize": "12px"},
                                    ),
                                ],
                            ),
                            _card(
                                title="Save sweeps",
                                children=[
                                    html.Div(
                                        [
                                            html.Label("File name (saved under ~/.osrs_ge_quant/reports/)"),
                                            dcc.Input(
                                                id="save-filename",
                                                type="text",
                                                value="sweep_latest.csv",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Button(
                                        "Save sweep as CSV",
                                        id="btn-save-sweep",
                                        n_clicks=0,
                                        style={"marginTop": "8px"},
                                    ),
                                    html.Div(
                                        id="save-sweep-status",
                                        style={"marginTop": "8px", "fontFamily": "monospace", "fontSize": "12px"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        style={"flex": "1"},
                        children=[
                            _card(
                                title="Auto-planner (turn sweeps into a single 'go' config)",
                                children=[
                                    html.Div(
                                        [
                                            html.Label("Sector filter (optional)"),
                                            dcc.Input(
                                                id="ap-sector-filter",
                                                type="text",
                                                placeholder="Leave blank for all sectors",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Min Sharpe"),
                                            dcc.Input(
                                                id="ap-min-sharpe",
                                                type="number",
                                                value=1.5,
                                                step=0.1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Max drawdown (decimal, e.g. -0.15)"),
                                            dcc.Input(
                                                id="ap-max-dd",
                                                type="number",
                                                value=-0.15,
                                                step=0.01,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Min total_return (e.g. 1.5 = +150%)"),
                                            dcc.Input(
                                                id="ap-min-return",
                                                type="number",
                                                value=1.5,
                                                step=0.1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Button(
                                        "Run auto-plan",
                                        id="btn-auto-plan",
                                        n_clicks=0,
                                        style={"marginTop": "12px"},
                                    ),
                                    html.Div(
                                        id="auto-plan-output",
                                        style={
                                            "marginTop": "12px",
                                            "fontFamily": "monospace",
                                            "whiteSpace": "pre-wrap",
                                            "fontSize": "12px",
                                        },
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def _layout_visuals_tab():
    return html.Div(
        style={"marginTop": "16px"},
        children=[
            _card(
                children=[
                    html.Div(
                        style={"display": "flex", "gap": "16px", "alignItems": "center"},
                        children=[
                            html.Div(
                                [
                                    html.Label("Metric for heatmap / ranking:"),
                                    dcc.Dropdown(
                                        id="metric-dropdown",
                                        options=[
                                            {"label": "total_return", "value": "total_return"},
                                            {"label": "sharpe", "value": "sharpe"},
                                            {"label": "max_drawdown", "value": "max_drawdown"},
                                            {"label": "final_equity", "value": "final_equity"},
                                        ],
                                        value="sharpe",
                                        clearable=False,
                                        style={"width": "220px"},
                                    ),
                                ]
                            ),
                            html.Div(
                                [
                                    html.Label("Sector filter (from sweeps)"),
                                    dcc.Dropdown(
                                        id="sector-dropdown",
                                        options=[{"label": "All sectors", "value": "__ALL__"}],
                                        value="__ALL__",
                                        clearable=False,
                                        style={"width": "220px"},
                                    ),
                                ]
                            ),
                            html.Div(
                                "Use this to see which (k_std, position_fraction) combos best fit a given sector.",
                                style={"fontSize": "12px", "color": "#666"},
                            ),
                        ],
                    )
                ]
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.H4("Heatmap (k_std vs position_fraction)"),
                            dcc.Graph(id="heatmap", style={"height": "450px"}),
                        ],
                        style={
                            "width": "50%",
                            "display": "inline-block",
                            "verticalAlign": "top",
                            "paddingRight": "8px",
                        },
                    ),
                    html.Div(
                        [
                            html.H4("Sharpe Surface (3D)"),
                            dcc.Graph(id="sharpe-surface", style={"height": "450px"}),
                        ],
                        style={
                            "width": "50%",
                            "display": "inline-block",
                            "verticalAlign": "top",
                            "paddingLeft": "8px",
                        },
                    ),
                ]
            ),
            html.Div(
                style={"marginTop": "16px"},
                children=[
                    html.H4("Top configurations (by selected metric)"),
                    dcc.Graph(id="results-table", style={"height": "400px"}),
                ],
            ),
        ],
    )


def _layout_screeners_tab():
    return html.Div(
        style={"marginTop": "16px"},
        children=[
            html.Div(
                style={"display": "flex", "gap": "24px"},
                children=[
                    html.Div(
                        style={"flex": "1"},
                        children=[
                            _card(
                                title="Run z-score screener (find cheap items now)",
                                children=[
                                    html.Div(
                                        [
                                            html.Label("Years of history (short horizon → 0.5–1)"),
                                            dcc.Input(
                                                id="scr-years",
                                                type="number",
                                                value=1,        # use at least 1 year for stable z-scores
                                                min=1,
                                                step=1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Timestep"),
                                            dcc.Input(
                                                id="scr-timestep",
                                                type="text",
                                                value="1d_weirdgloop",
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("z-score cutoff (e.g. -1.0 → cheap vs history)"),
                                            dcc.Input(
                                                id="scr-z-cutoff",
                                                type="number",
                                                value=-1.0,
                                                step=0.1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Limit rows"),
                                            dcc.Input(
                                                id="scr-limit",
                                                type="number",
                                                value=100,
                                                min=10,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Button(
                                        "Run screener",
                                        id="btn-run-screener",
                                        n_clicks=0,
                                        style={"marginTop": "12px"},
                                    ),
                                    html.Div(
                                        id="screener-status",
                                        style={"marginTop": "12px", "fontFamily": "monospace", "fontSize": "12px"},
                                    ),
                                ],
                            ),
                            _card(
                                title="Quick flip sizing (no-bullshit: what and how much to buy)",
                                children=[
                                    html.Div(
                                        [
                                            html.Label("Total capital for flips (gp)"),
                                            dcc.Input(
                                                id="qf-total-capital",
                                                type="number",
                                                value=50_000_000,
                                                min=1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Position fraction (per-item size, e.g. 0.05 = 5%)"),
                                            dcc.Input(
                                                id="qf-position-fraction",
                                                type="number",
                                                value=0.05,
                                                step=0.01,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Max positions (how many items to flip at once)"),
                                            dcc.Input(
                                                id="qf-max-positions",
                                                type="number",
                                                value=10,
                                                min=1,
                                                style={"width": "100%"},
                                            ),
                                        ]
                                    ),
                                    html.Button(
                                        "Compute quick flip suggestions",
                                        id="btn-run-quick-flips",
                                        n_clicks=0,
                                        style={"marginTop": "12px"},
                                    ),
                                    html.Div(
                                        id="quick-flips-status",
                                        style={"marginTop": "12px", "fontFamily": "monospace", "fontSize": "12px"},
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        style={"flex": "2"},
                        children=[
                            _card(
                                title="Screener results (detailed view)",
                                children=[
                                    dcc.Graph(
                                        id="screener-table",
                                        style={"height": "330px"},
                                    ),
                                    html.Div(
                                        "Interpretation: lower z_score = cheaper vs its own history. "
                                        "Combine with liquidity (vol) and your own knowledge of the item.",
                                        style={"fontSize": "12px", "color": "#666", "marginTop": "4px"},
                                    ),
                                ],
                            ),
                            _card(
                                title="Quick flip suggestions (actionable sizing)",
                                children=[
                                    dcc.Graph(
                                        id="quick-flips-table",
                                        style={"height": "330px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Div(
                                                "Interpretation:",
                                                style={"fontWeight": "bold", "fontSize": "12px"},
                                            ),
                                            html.Ul(
                                                style={"fontSize": "12px", "color": "#666"},
                                                children=[
                                                    html.Li("qty_suggested: how many to buy for that item."),
                                                    html.Li("notional_gp: gp committed to that item."),
                                                    html.Li("risk: Aggressive = big z-move & cheap item."),
                                                    html.Li(
                                                        "This is still your decision: check GE limits, game updates, and your own risk appetite."
                                                    ),
                                                ],
                                            ),
                                        ]
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def _layout_portfolio_tab():
    return html.Div(
        style={"marginTop": "16px"},
        children=[
            _card(
                title="Portfolio – open positions",
                children=[
                    html.Button(
                        "Refresh portfolio",
                        id="btn-refresh-portfolio",
                        n_clicks=0,
                        style={"marginBottom": "8px"},
                    ),
                    html.Div(
                        id="portfolio-status",
                        style={"marginBottom": "8px", "fontFamily": "monospace", "fontSize": "12px"},
                    ),
                    dcc.Graph(id="portfolio-table", style={"height": "450px"}),
                    html.Div(
                        "As you log trades (from CLI or Trades tab), this view gives you live exposure.",
                        style={"fontSize": "12px", "color": "#666", "marginTop": "4px"},
                    ),
                ],
            )
        ],
    )


def _layout_trades_tab():
    return html.Div(
        style={"marginTop": "16px", "maxWidth": "600px"},
        children=[
            _card(
                title="Log trade (GUI wrapper around log-trade CLI)",
                children=[
                    html.Div(
                        [
                            html.Label("Account name (e.g. Main)"),
                            dcc.Input(
                                id="trade-account",
                                type="text",
                                value="Main",
                                style={"width": "100%"},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Label("Side"),
                            dcc.Dropdown(
                                id="trade-side",
                                options=[
                                    {"label": "Buy", "value": "buy"},
                                    {"label": "Sell", "value": "sell"},
                                ],
                                value="buy",
                                clearable=False,
                                style={"width": "100%"},
                            ),
                        ],
                        style={"marginTop": "8px"},
                    ),
                    html.Div(
                        [
                            html.Label("Item ID"),
                            dcc.Input(
                                id="trade-item-id",
                                type="number",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"marginTop": "8px"},
                    ),
                    html.Div(
                        [
                            html.Label("Item name"),
                            dcc.Input(
                                id="trade-item-name",
                                type="text",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"marginTop": "8px"},
                    ),
                    html.Div(
                        [
                            html.Label("Quantity"),
                            dcc.Input(
                                id="trade-qty",
                                type="number",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"marginTop": "8px"},
                    ),
                    html.Div(
                        [
                            html.Label("Price each (gp)"),
                            dcc.Input(
                                id="trade-price-each",
                                type="number",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"marginTop": "8px"},
                    ),
                    html.Div(
                        [
                            html.Label("Note (optional)"),
                            dcc.Input(
                                id="trade-note",
                                type="text",
                                style={"width": "100%"},
                            ),
                        ],
                        style={"marginTop": "8px"},
                    ),
                    html.Button(
                        "Submit trade",
                        id="btn-submit-trade",
                        n_clicks=0,
                        style={"marginTop": "12px"},
                    ),
                    html.Div(
                        id="trade-status",
                        style={"marginTop": "12px", "fontFamily": "monospace", "fontSize": "12px"},
                    ),
                    html.Div(
                        "After logging a trade, switch to the Portfolio tab and click "
                        "'Refresh portfolio' to see it reflected.",
                        style={"marginTop": "12px", "fontSize": "12px", "color": "#555"},
                    ),
                ],
            )
        ],
    )


def _layout_event_tab():
    return html.Div(
        style={"marginTop": "16px"},
        children=[
            html.Div(
                style={"display": "flex", "gap": "24px"},
                children=[
                    html.Div(
                        style={"flex": "1"},
                        children=[
                            _card(
                                title="Event configuration",
                                children=[
                                    html.Div(
                                        [
                                            html.Label("Event date"),
                                            dcc.DatePickerSingle(
                                                id="event-date",
                                                display_format="YYYY-MM-DD",
                                            ),
                                        ]
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Pre-window (days before)"),
                                            dcc.Input(
                                                id="event-pre-days",
                                                type="number",
                                                value=7,
                                                min=1,
                                                style={"width": "100%"},
                                            ),
                                        ],
                                        style={"marginTop": "8px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Post-window (days after)"),
                                            dcc.Input(
                                                id="event-post-days",
                                                type="number",
                                                value=7,
                                                min=1,
                                                style={"width": "100%"},
                                            ),
                                        ],
                                        style={"marginTop": "8px"},
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Timestep"),
                                            dcc.Input(
                                                id="event-timestep",
                                                type="text",
                                                value="1d_weirdgloop",
                                                style={"width": "100%"},
                                            ),
                                        ],
                                        style={"marginTop": "8px"},
                                    ),
                                    html.Button(
                                        "Run event study",
                                        id="btn-run-event-study",
                                        n_clicks=0,
                                        style={"marginTop": "12px"},
                                    ),
                                    html.Div(
                                        id="event-status",
                                        style={"marginTop": "12px", "fontFamily": "monospace", "fontSize": "12px"},
                                    ),
                                    html.Div(
                                        "Use this after big game updates. It shows how the average item reacted "
                                        "before/after the patch.",
                                        style={"marginTop": "8px", "fontSize": "12px", "color": "#666"},
                                    ),
                                ],
                            )
                        ],
                    ),
                    html.Div(
                        style={"flex": "2"},
                        children=[
                            _card(
                                title="Average / median return vs days offset",
                                children=[
                                    dcc.Graph(id="event-graph", style={"height": "360px"}),
                                ],
                            ),
                            _card(
                                title="Per-offset summary",
                                children=[
                                    dcc.Graph(id="event-table", style={"height": "260px"}),
                                ],
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


# ------------------------------
# Entrypoint
# ------------------------------
def main() -> None:
    parser = argparse.ArgumentParser("osrs-ge-quant dashboard")
    parser.add_argument("csv", nargs="?", help="Optional sweep CSV to preload")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            raise SystemExit(f"CSV not found: {csv_path}")
        df = pd.read_csv(csv_path)
    else:
        df = pd.DataFrame()

    app = build_app(df)
    # For LAN/web later, run with --host 0.0.0.0
    app.run(debug=True, port=args.port, host=args.host)


if __name__ == "__main__":
    main()
