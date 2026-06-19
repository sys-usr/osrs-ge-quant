# src/osrs_ge_quant/cli.py

from __future__ import annotations

import argparse
from datetime import datetime
from typing import List

# Local imports
from osrs_ge_quant.db import init_db, get_session
from osrs_ge_quant.models import Account, Recommendation, Trade
from osrs_ge_quant.config import load_accounts_config
from osrs_ge_quant.ge_api import refresh_universe, update_timeseries
from osrs_ge_quant.engine import run_full_cycle
from osrs_ge_quant.news import fetch_news_archive, fetch_news_details
from osrs_ge_quant.news_analyzer import analyze_unprocessed_news
from osrs_ge_quant.webapp.app import run_dashboard
from osrs_ge_quant.ml_lstm import train_lstm
from osrs_ge_quant.speculator import run_speculation_cycle
from osrs_ge_quant.discord_bot import start_discord_bot

from osrs_ge_quant.backtest import backtest_flip_strategy, sweep_backtests
from osrs_ge_quant.screeners import run_zscore_screener
from osrs_ge_quant.portfolio import load_open_positions, mark_to_market, summarize_portfolio
from osrs_ge_quant.event_study import run_event_study


# --------------------------
# Core commands
# --------------------------

def cmd_init_db(args):
    print("[CLI] Initializing DB...")
    init_db()
    session = get_session()

    accounts_cfg = load_accounts_config()
    for a in accounts_cfg:
        existing = session.query(Account).filter_by(name=a["name"]).first()
        if existing:
            continue
        session.add(Account(**a))
    session.commit()
    session.close()
    print("[CLI] DB initialized and accounts seeded.")


def cmd_refresh_universe(args):
    print("[CLI] Refreshing GE universe (mapping + 24h prices)...")
    refresh_universe()
    print("[CLI] Universe refresh complete.")


def cmd_update_timeseries(args):
    timestep = args.timestep
    print(f"[CLI] Updating timeseries for timestep={timestep}...")
    update_timeseries(timestep=timestep)


def cmd_cron_refresh(args):
    print("[CLI] Cron refresh: 24h snapshot + timeseries + analyze")
    refresh_universe()
    update_timeseries(timestep=args.timestep)
    run_full_cycle()
    print("[CLI] Cron refresh complete.")


def cmd_analyze(args):
    print("[CLI] Running full analysis cycle...")
    run_full_cycle()
    print("[CLI] Analysis complete and recommendations stored.")


def cmd_dashboard(args):
    print("[CLI] Starting Discord Command Bot Daemon...")
    start_discord_bot()
    print("[CLI] Starting dashboard at http://127.0.0.1:8050 ...")
    run_dashboard()


def cmd_train_lstm(args):
    print("[CLI] Training PyTorch LSTM Sequence model...")
    res = train_lstm()
    if "error" in res:
        print("[CLI] Error training LSTM:", res["error"])
    else:
        print(f"[CLI] LSTM Training complete. RMSE: {res['rmse']:.6f}. MAE: {res['mae']:.6f}")


def cmd_train_dqn(args):
    print("[CLI] Training PyTorch DQN Pricing model...")
    from osrs_ge_quant.ml_dqn import DQNPricingAgent, sync_replay_buffer_from_db
    from osrs_ge_quant.config import data_dir
    import os
    # pyrefly: ignore [missing-import]
    import numpy as np
    
    agent = DQNPricingAgent()
    
    model_path = os.path.join(data_dir(), "dqn_model.pth")
    if os.path.exists(model_path):
        print(f"[CLI] Loading existing model weights from {model_path}...")
        agent.load_model(model_path)
        
    sync_replay_buffer_from_db(agent, limit=10000)
    
    buffer_len = len(agent.replay_buffer)
    if buffer_len < agent.batch_size:
        print(f"[CLI] Replay buffer size ({buffer_len}) is less than batch size ({agent.batch_size}).")
        print("[CLI] Bootstrapping replay buffer with simulated OSRS order book transition logs...")
        
        for _ in range(500):
            current_price = float(np.random.randint(1000, 10000000))
            spread = float(current_price * np.random.uniform(0.01, 0.08))
            buy_depth = float(np.random.randint(10, 2000))
            sell_depth = float(np.random.randint(10, 2000))
            
            norm_spread = spread / (current_price + 1e-9)
            log_buy = np.log1p(buy_depth)
            log_sell = np.log1p(sell_depth)
            imbalance = (buy_depth - sell_depth) / (buy_depth + sell_depth + 1e-9)
            state = np.array([norm_spread, log_buy, log_sell, imbalance, 1.0], dtype=np.float32)
            
            action = np.random.randint(0, 10)
            
            if action in [4, 5, 6, 9]:
                reward = float(spread * np.random.uniform(0.1, 0.9))
            else:
                reward = float(-spread * np.random.uniform(0.05, 0.3))
                
            next_price = current_price + np.random.randint(-50, 50)
            next_spread = float(next_price * np.random.uniform(0.01, 0.08))
            next_buy_depth = float(np.random.randint(10, 2000))
            next_sell_depth = float(np.random.randint(10, 2000))
            next_state = np.array([
                next_spread / (next_price + 1e-9),
                np.log1p(next_buy_depth),
                np.log1p(next_sell_depth),
                (next_buy_depth - next_sell_depth) / (next_buy_depth + next_sell_depth + 1e-9),
                1.0
            ], dtype=np.float32)
            
            done = bool(np.random.choice([True, False]))
            agent.replay_buffer.push(state, action, reward, next_state, done)
            
        buffer_len = len(agent.replay_buffer)
        
    print(f"[CLI] Starting DQN training loop with {buffer_len} experiences in buffer...")
    losses = []
    
    epochs = args.epochs
    for step in range(1, epochs + 1):
        loss = agent.train_step()
        if loss is not None:
            losses.append(loss)
            
        if step % 10 == 0:
            agent.update_target_network()
            
        if step % 20 == 0 and losses:
            avg_loss = sum(losses[-20:]) / len(losses[-20:])
            print(f"  Step {step}/{epochs} - Average Loss: {avg_loss:.6f} - Epsilon: {agent.epsilon:.4f}")
            
        agent.update_epsilon()
        
    agent.save_model(model_path)
    print(f"[CLI] DQN Training complete. Model weights saved to {model_path}")


def cmd_speculate(args):
    print("[CLI] Running Speculation and news analysis cycle...")
    run_speculation_cycle()


def cmd_run_bot(args):
    print("[CLI] Launching Discord Command Bot Daemon standalone...")
    start_discord_bot()
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[CLI] Bot shutdown complete.")


def cmd_daemon(args):
    from osrs_ge_quant.sentinel import run_sentinel_daemon
    run_sentinel_daemon()


def cmd_log_trade(args):
    session = get_session()
    account = session.query(Account).filter_by(name=args.account).one()

    t = Trade(
        ts=datetime.utcnow(),
        account_id=account.id,
        item_id=args.item_id,
        item_name=args.item_name,
        side=args.side,
        qty=args.qty,
        price_each=args.price_each,
        note=args.note,
    )
    session.add(t)

    if args.rec_id:
        rec = session.get(Recommendation, args.rec_id)
        if rec:
            rec.taken_trade = t

    session.commit()
    session.close()
    print(
        f"[CLI] Logged {args.side} trade on {args.account}: "
        f"{args.qty}x {args.item_name} @ {args.price_each} gp"
    )


def cmd_skip_rec(args):
    session = get_session()
    rec = session.get(Recommendation, args.rec_id)
    if not rec:
        print(f"[CLI] Recommendation {args.rec_id} not found.")
        return
    rec.skipped = True
    session.commit()
    session.close()
    print(f"[CLI] Marked recommendation {args.rec_id} as skipped.")


# --------------------------
# Backtest / research
# --------------------------

def cmd_backtest(args):
    res = backtest_flip_strategy(
        years=args.years,
        timestep=args.timestep,
        initial_capital=args.initial_capital,
        k_std=args.k_std,
        position_fraction=args.position_fraction,
        fee_rate=args.fee_rate,
        top_n=args.top_n,
    )

    if "error" in res and not res.get("metrics"):
        print("[CLI] Backtest error:", res["error"])
        return

    cfg = res.get("config", {})
    m = res.get("metrics", {})

    print("\n[CLI] Backtest result:")
    print("-" * 40)
    print(
        f"Universe: top {cfg.get('top_n')} items | "
        f"Timestep: {cfg.get('timestep')} | "
        f"Fee: {cfg.get('fee_rate', 0)*100:.2f}%"
    )
    print(f"Initial capital: {cfg.get('initial_capital'):,.0f} gp")
    print("-" * 40)
    print(f"Final equity:   {m.get('final_equity', 0):,.0f} gp")
    print(f"Total return:   {m.get('total_return', 0)*100:.2f}%")
    print(f"Sharpe ratio:   {m.get('sharpe', 0):.2f}")
    print(f"Max drawdown:   {m.get('max_drawdown', 0)*100:.2f}%")
    print("-" * 40)

    per_item = res.get("per_item_stats", [])[:10]
    if per_item:
        print("Top 10 items by PnL:")
        for row in per_item:
            print(
                f"  {row.get('item_id'):>6} {row.get('name') or '???':<30} "
                f"P&L: {row.get('pnl', 0):>12.0f} gp "
                f"Trades: {row.get('n_trades', 0):>4}"
            )


def cmd_backtest_sweep(args):
    k_vals = [float(x) for x in args.k_std_grid.split(",")]
    pf_vals = [float(x) for x in args.position_grid.split(",")]

    results = sweep_backtests(
        years=args.years,
        timestep=args.timestep,
        initial_capital=args.initial_capital,
        k_std_values=k_vals,
        position_fractions=pf_vals,
        fee_rate=args.fee_rate,
        top_n=args.top_n,
    )

    print("\n[CLI] Backtest sweep results:")
    print("k_std\tpos_frac\tret(%)\tsharpe\tmaxDD(%)")
    for r in results:
        print(
            f"{r['k_std']:.2f}\t"
            f"{r['position_fraction']:.3f}\t"
            f"{(r['total_return'] or 0)*100:6.2f}\t"
            f"{(r['sharpe'] or 0):5.2f}\t"
            f"{(r['max_drawdown'] or 0)*100:7.2f}"
        )


# --------------------------
# Screeners / portfolio
# --------------------------

def cmd_screen(args):
    df = run_zscore_screener(
        years=args.years,
        timestep=args.timestep,
        rolling_window=args.rolling_window,
        min_history=args.min_history,
        top_n_by_liquidity=args.top_n,
        z_cutoff=args.z_cutoff,
        limit=args.limit,
    )

    if df.empty:
        print("[CLI] Screener returned no rows.")
        return

    print(
        f"[CLI] Screener results (top {len(df)} items) "
        f"sorted by z_score ascending:"
    )
    for _, row in df.iterrows():
        print(
            f"{int(row['item_id']):>6} "
            f"{str(row['name'])[:28]:<28} "
            f"price={row['last_price']:>9.0f} "
            f"z={row['z_score']:+5.2f}"
        )


def cmd_portfolio(args):
    positions = load_open_positions()
    if positions.empty:
        print("[CLI] No open positions found.")
        return

    marked = mark_to_market(positions, timestep=args.timestep)
    summary = summarize_portfolio(marked)

    print("\n[CLI] Portfolio summary:")
    print("-" * 40)
    print(f"Total MV:       {summary['total_market_value']:,.0f} gp")
    print(f"Unrealized PnL: {summary['total_unrealized_pnl']:,.0f} gp")
    print(f"Positions:      {summary['n_positions']}")
    print("-" * 40)

    print("By account:")
    for acct, mv in summary["by_account"].items():
        print(f"  {acct:<20} {mv:>12,.0f} gp")

    print("\nTop contributors:")
    for row in summary["top_contributors"]:
        item_name = str(row.get('item_name') or 'Unknown')
        print(
            f"  {row['item_id']:>6} {item_name[:28]:<28} "
            f"P&L: {row['unrealized_pnl']:>12.0f} gp"
        )


# --------------------------
# News / event study
# --------------------------

def cmd_fetch_news(args):
    posts = fetch_news_archive()
    print(f"[NEWS] Added {len(posts)} new archive entries.")
    for p in posts:
        fetch_news_details(p)


def cmd_analyze_news(args):
    analyze_unprocessed_news()
    print("[CLI] News analysis complete.")


def cmd_backfill_timeseries(args):
    from osrs_ge_quant.ge_api import backfill_high_resolution_history
    print(f"[CLI] Backfilling high-resolution timeseries for timestep={args.timestep}, top_n={args.top_n}...")
    backfill_high_resolution_history(timestep=args.timestep, top_n_items=args.top_n)


def cmd_event_study(args):
    # item_ids passed as comma-separated list
    item_ids: List[int] = [int(x) for x in args.item_ids.split(",") if x.strip()]
    event_ts = datetime.fromisoformat(args.event_ts)

    res = run_event_study(
        item_ids=item_ids,
        event_ts=event_ts,
        window_pre=args.window_pre,
        window_post=args.window_post,
        timestep=args.timestep,
    )

    if "error" in res:
        print("[CLI] Event study error:", res["error"])
        return

    avg = res["average"]
    print("[CLI] Event study (average cumulative return):")
    print("rel_day\tcum_ret(%)")
    for d, c in zip(avg["rel_days"], avg["avg_cum_return"]):
        print(f"{d:+3d}\t{c*100:6.2f}")


def cmd_train_model(args):
    from osrs_ge_quant.ml_predictor import train_flip_model
    res = train_flip_model()
    if "error" in res:
        print("[CLI] Model training failed:", res["error"])
    else:
        print(f"[CLI] Model training complete. Sample size: {res['sample_count']}. Out-of-sample MAE: {res['mae']:.0f} gp. Accuracy: {res['accuracy']*100:.1f}%.")


def cmd_evaluate_sentiment(args):
    from osrs_ge_quant.sentiment_evaluator import evaluate_sentiment_performance
    res = evaluate_sentiment_performance()
    if not res:
        print("[CLI] No sentiment data to evaluate. Run news scraping and analysis first.")
        return
    
    print("\n[CLI] Sentiment Predictive Accuracy Report:")
    print("-" * 75)
    print(f"{'Source':<10} | {'Count':<6} | {'3d Acc (%)':<10} | {'7d Acc (%)':<10} | {'Mean UP 7d (%)':<14} | {'Mean DOWN 7d (%)':<16}")
    print("-" * 75)
    for cat, stats in res.items():
        print(
            f"{cat:<10} | "
            f"{stats['count']:<6} | "
            f"{stats['accuracy_3d']:<10.2f} | "
            f"{stats['accuracy_7d']:<10.2f} | "
            f"{stats['mean_return_up_7d']:<14.2f} | "
            f"{stats['mean_return_down_7d']:<16.2f}"
        )
    print("-" * 75)


# --------------------------
# Parser
# --------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("osrs-ge-quant")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Initialize DB schema and seed accounts")
    sub.add_parser("refresh-universe", help="Refresh all GE items + prices")

    ts = sub.add_parser("update-timeseries", help="Fetch and store timeseries prices")
    ts.add_argument("--timestep", default="1h")

    cron = sub.add_parser(
        "cron-refresh",
        help="Scheduler entry: refresh universe + timeseries + analyze",
    )
    cron.add_argument("--timestep", default="1h")

    sub.add_parser("analyze", help="Run full analysis cycle and store recommendations")
    sub.add_parser("dashboard", help="Run the Dash web dashboard")
    sub.add_parser("daemon", help="Run the continuous updating and alerting engine loop")

    log = sub.add_parser("log-trade", help="Log a buy/sell in the database")
    log.add_argument("account", help="Account name (e.g. Main)")
    log.add_argument("side", choices=["buy", "sell"])
    log.add_argument("item_id", type=int)
    log.add_argument("item_name")
    log.add_argument("qty", type=int)
    log.add_argument("price_each", type=int)
    log.add_argument("--note", default="")
    log.add_argument("--rec-id", type=int, help="Recommendation ID")

    mark_skip = sub.add_parser("skip-rec", help="Mark a recommendation as skipped")
    mark_skip.add_argument("rec_id", type=int)

    # Backtest
    bt = sub.add_parser("backtest", help="Run mean-reversion flip backtest")
    bt.add_argument("--years", type=int, default=3)
    bt.add_argument("--timestep", default="1d_weirdgloop")
    bt.add_argument("--initial-capital", type=int, default=100_000_000)
    bt.add_argument("--k-std", type=float, default=1.0)
    bt.add_argument("--position-fraction", type=float, default=0.05)
    bt.add_argument("--fee-rate", type=float, default=0.01)
    bt.add_argument("--top-n", type=int, default=300)

    # Backtest sweep
    bts = sub.add_parser(
        "backtest-sweep",
        help="Grid search over (k_std, position_fraction)",
    )
    bts.add_argument("--years", type=int, default=3)
    bts.add_argument("--timestep", default="1d_weirdgloop")
    bts.add_argument("--initial-capital", type=int, default=100_000_000)
    bts.add_argument(
        "--k-std-grid",
        default="0.8,1.0,1.2",
        help="Comma-separated k_std values, e.g. '0.8,1.0,1.2'",
    )
    bts.add_argument(
        "--position-grid",
        default="0.03,0.05,0.08",
        help="Comma-separated position_fraction values, e.g. '0.03,0.05,0.08'",
    )
    bts.add_argument("--fee-rate", type=float, default=0.01)
    bts.add_argument("--top-n", type=int, default=300)

    # Screener
    scr = sub.add_parser("screen", help="Z-score / liquidity screener")
    scr.add_argument("--years", type=int, default=1)
    scr.add_argument("--timestep", default="1d_weirdgloop")
    scr.add_argument("--rolling-window", type=int, default=50)
    scr.add_argument("--min-history", type=int, default=60)
    scr.add_argument("--top-n", type=int, default=500)
    scr.add_argument("--z-cutoff", type=float, default=-1.0)
    scr.add_argument("--limit", type=int, default=50)

    # Portfolio
    pf = sub.add_parser("portfolio", help="Show portfolio PnL / risk summary")
    pf.add_argument("--timestep", default="24h")

    # News / events
    sub.add_parser("fetch-news", help="Fetch latest RS news posts into DB")
    sub.add_parser("analyze-news", help="Analyze news posts with ChatGPT")

    bts_backfill = sub.add_parser(
        "backfill-timeseries",
        help="Backfill high-resolution historical prices from OSRS Wiki prices API",
    )
    bts_backfill.add_argument("--timestep", default="5m", choices=["5m", "1h", "6h"])
    bts_backfill.add_argument("--top-n", type=int, default=300)

    ev = sub.add_parser("event-study", help="Run event study around a date")
    ev.add_argument(
        "item_ids",
        help="Comma-separated item IDs, e.g. '2,1323,257'",
    )
    ev.add_argument(
        "event_ts",
        help="Event timestamp (ISO), e.g. '2025-11-20T00:00:00'",
    )
    ev.add_argument("--window-pre", type=int, default=7)
    ev.add_argument("--window-post", type=int, default=21)
    ev.add_argument("--timestep", default="1d_weirdgloop")

    sub.add_parser("train-model", help="Train Random Forest models on historical flip details")
    sub.add_parser("evaluate-sentiment", help="Evaluate historical news/sentiment predictive accuracy")
    sub.add_parser("train-lstm", help="Train PyTorch LSTM forecasting model on hourly price logs")
    
    dqn_parser = sub.add_parser("train-dqn", help="Train PyTorch DQN pricing model on federated DB experiences")
    dqn_parser.add_argument("--epochs", type=int, default=100, help="Number of updates to train")
    
    sub.add_parser("speculate", help="Run Jagex news and Reddit speculation cycles to generate news buy signals")
    sub.add_parser("run-bot", help="Run Discord Bot daemon standalone")

    return p


# --------------------------
# Entry point
# --------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "init-db": cmd_init_db,
        "refresh-universe": cmd_refresh_universe,
        "update-timeseries": cmd_update_timeseries,
        "cron-refresh": cmd_cron_refresh,
        "analyze": cmd_analyze,
        "dashboard": cmd_dashboard,
        "daemon": cmd_daemon,
        "log-trade": cmd_log_trade,
        "skip-rec": cmd_skip_rec,
        "backtest": cmd_backtest,
        "backtest-sweep": cmd_backtest_sweep,
        "screen": cmd_screen,
        "portfolio": cmd_portfolio,
        "fetch-news": cmd_fetch_news,
        "analyze-news": cmd_analyze_news,
        "backfill-timeseries": cmd_backfill_timeseries,
        "event-study": cmd_event_study,
        "train-model": cmd_train_model,
        "evaluate-sentiment": cmd_evaluate_sentiment,
        "train-lstm": cmd_train_lstm,
        "train-dqn": cmd_train_dqn,
        "speculate": cmd_speculate,
        "run-bot": cmd_run_bot,
    }

    fn = commands.get(args.cmd)
    if fn is None:
        parser.print_help()
    else:
        fn(args)


if __name__ == "__main__":
    main()

