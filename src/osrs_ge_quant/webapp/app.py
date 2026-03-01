# src/osrs_ge_quant/webapp/app.py
from dash import Dash, dcc, html, dash_table
import plotly.express as px
import pandas as pd
from sqlalchemy import select

from .backtest_tab import build_backtest_tab
from ..db import get_session
from ..models import Account, Trade, Recommendation
from ..config import load_settings


def load_summary():
    session = get_session()

    accounts = session.query(Account).all()
    trades = session.query(Trade).all()
    recs = session.query(Recommendation).all()

    # Approx PnL per account using expected_profit_gp of taken recs
    pnl_per_account = {a.name: 0.0 for a in accounts}
    for t in trades:
        if t.recommendation and t.recommendation.expected_profit_gp:
            pnl_per_account[t.account.name] += t.recommendation.expected_profit_gp

    acc_rows = []
    for a in accounts:
        start_gp = a.starting_gp
        pnl = pnl_per_account[a.name]
        acc_rows.append(
            {
                "Account": a.name,
                "RSN": a.rsn,
                "Starting GP": start_gp,
                "Approx PnL": pnl,
                "Approx Current GP": start_gp + pnl,
                "Role": a.role,
                "Active": a.active,
            }
        )
    acc_df = pd.DataFrame(acc_rows) if acc_rows else pd.DataFrame(
        columns=["Account", "RSN", "Starting GP", "Approx PnL", "Approx Current GP", "Role", "Active"]
    )

    rec_rows = []
    for r in recs:
        rec_rows.append(
            {
                "id": r.id,
                "strategy": r.strategy_name,
                "item_id": r.item_id,
                "side": r.side,
                "qty": r.qty,
                "price_each": r.price_each,
                "expected_profit_gp": r.expected_profit_gp,
                "expected_return_pct": r.expected_return_pct,
                "taken": r.taken_trade_id is not None,
                "skipped": r.skipped,
                "signal_type": r.signal_type,
                "reason": r.reason,
                "created_at": r.created_at,
            }
        )
    rec_df = pd.DataFrame(rec_rows) if rec_rows else pd.DataFrame(
        columns=[
            "id",
            "strategy",
            "item_id",
            "side",
            "qty",
            "price_each",
            "expected_profit_gp",
            "expected_return_pct",
            "taken",
            "skipped",
            "signal_type",
            "reason",
            "created_at",
        ]
    )

    return acc_df, rec_df


def create_app():
    settings = load_settings()
    app = Dash(__name__)

    acc_df, rec_df = load_summary()

    # Separate taken vs skipped vs untouched
    taken_df = rec_df[rec_df["taken"] == True] if not rec_df.empty else rec_df
    skipped_df = rec_df[(rec_df["taken"] == False) & (rec_df["skipped"] == True)] if not rec_df.empty else rec_df

    # Histogram figure of expected profit
    if not rec_df.empty:
        hist_fig = px.histogram(
            rec_df,
            x="expected_profit_gp",
            color=rec_df["taken"].map({True: "Taken", False: "Not taken"}),
            nbins=50,
            title="Expected Profit Distribution (Taken vs Not Taken)",
        )
    else:
        hist_fig = px.histogram(title="No recommendations yet")

    app.layout = html.Div(
        style={"margin": "20px"},
        children=[
            html.H1("OSRS GE Quant Dashboard"),
            html.Hr(),

            html.H2("Accounts"),
            dash_table.DataTable(
                data=acc_df.to_dict("records"),
                columns=[{"name": c, "id": c} for c in acc_df.columns],
                page_size=10,
                sort_action="native",
                filter_action="native",
                style_table={"overflowX": "auto"},
            ),

            html.Br(),
            html.H2("Expected Profit (Taken vs Not Taken)"),
            dcc.Graph(figure=hist_fig),

            html.Br(),
            html.H2("Top Taken Recommendations"),
            dash_table.DataTable(
                data=(
                    taken_df.sort_values("expected_profit_gp", ascending=False)
                    .head(20)
                    .to_dict("records")
                    if not taken_df.empty
                    else []
                ),
                columns=[{"name": c, "id": c} for c in rec_df.columns],
                page_size=20,
                style_table={"overflowX": "auto"},
            ),
            build_backtest_tab(),
            html.Br(),
            html.H2("Top Skipped Recommendations"),
            dash_table.DataTable(
                data=(
                    skipped_df.sort_values("expected_profit_gp", ascending=False)
                    .head(20)
                    .to_dict("records")
                    if not skipped_df.empty
                    else []
                ),
                columns=[{"name": c, "id": c} for c in rec_df.columns],
                page_size=20,
                style_table={"overflowX": "auto"},
            ),
        ],
    )

    return app


def run_dashboard():
    app = create_app()
    settings = load_settings()
    host = settings["dashboard"]["host"]
    port = settings["dashboard"]["port"]
    debug = settings["dashboard"]["debug"]

    print(f"[DASH] Running dashboard at http://{host}:{port}")
    # Dash 3.x uses app.run(), not run_server()
    app.run(host=host, port=port, debug=debug)
