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
    print("[CLI] Starting dashboard at http://127.0.0.1:8050 ...")
    run_dashboard()


def cmd_daemon(args):
    import time
    from osrs_ge_quant.config import load_settings
    print("[CLI] Starting continuous Day-Trading Daemon...")
    settings = load_settings()
    daemon_settings = settings.get("daemon", {})
    interval_mins = daemon_settings.get("interval_minutes", 5)
    print(f"[Daemon] Interval configured: {interval_mins} minutes.")
    
    last_digest_time = None
    
    try:
        while True:
            start_time = time.time()
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"\n[Daemon] [{now_str}] Starting cycle update...")
            try:
                # 1. Fetch and analyze OSRS news & Reddit posts
                print("[Daemon] Fetching OSRS news updates...")
                posts = fetch_news_archive()
                print(f"[Daemon] Fetched {len(posts)} new archive entries.")
                for p in posts:
                    fetch_news_details(p)
                
                print("[Daemon] Fetching YouTube updates...")
                from osrs_ge_quant.news import fetch_youtube_feed
                yt_posts = fetch_youtube_feed()
                print(f"[Daemon] Fetched {len(yt_posts)} new YouTube video uploads.")
                
                print("[Daemon] Scraping r/2007scape Reddit discussions...")
                from osrs_ge_quant.reddit import scrape_reddit
                scrape_reddit()
                
                print("[Daemon] Running sentiment analysis on news & Reddit posts...")
                analyze_unprocessed_news()

                
                # 2. Run price cycle & hot flip checks
                # Send digest on first run or every 12 hours
                should_send_digest = (last_digest_time is None or (time.time() - last_digest_time) >= 12 * 3600)
                print(f"[Daemon] Running full analysis cycle (send_digest={should_send_digest})...")
                run_full_cycle(send_digest=should_send_digest)
                if should_send_digest:
                    last_digest_time = time.time()
                    
                print(f"[Daemon] [{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] Cycle completed successfully.")
            except Exception as e:
                import traceback
                print(f"[Daemon] [Error] Exception in cycle execution: {e}")
                traceback.print_exc()
            
            elapsed = time.time() - start_time
            sleep_time = max(0.0, (interval_mins * 60.0) - elapsed)
            print(f"[Daemon] Sleeping for {sleep_time / 60.0:.2f} minutes until next update.")
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n[Daemon] KeyboardInterrupt received. Shutting down daemon gracefully.")


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
        print(
            f"  {row['item_id']:>6} {row['item_name'][:28]:<28} "
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
    }

    fn = commands.get(args.cmd)
    if fn is None:
        parser.print_help()
    else:
        fn(args)


if __name__ == "__main__":
    main()

