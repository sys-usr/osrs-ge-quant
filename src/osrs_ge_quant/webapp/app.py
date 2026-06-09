# src/osrs_ge_quant/webapp/app.py

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, request, render_template
import pandas as pd
import numpy as np
from sqlalchemy import select, func

from ..db import get_session
from ..models import Account, Trade, Recommendation, Item, PricePoint, NewsPost, NewsImpact
from ..config import load_settings, save_settings, load_strategies, save_strategies
from ..portfolio import load_open_positions, mark_to_market, summarize_portfolio
from ..strategy import calculate_osrs_tax
from ..backtest.engine import backtest_flip_strategy

app = Flask(__name__, template_folder="templates")

# Lock to prevent concurrent execution of news scrapers and GE price cycles
daemon_lock = threading.Lock()

# --- Flask Server Routes ---

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/summary")
def get_dashboard_summary():
    session = get_session()
    settings = load_settings()
    
    # 1. Accounts
    accounts = session.query(Account).all()
    trades = session.query(Trade).all()

    # Calculate PnL per account from DB trades
    pnl_per_account = {a.name: 0.0 for a in accounts}
    for t in trades:
        if t.recommendation and t.recommendation.expected_profit_gp:
            pnl_per_account[t.account.name] += t.recommendation.expected_profit_gp

    accounts_list = []
    for a in accounts:
        pnl = pnl_per_account.get(a.name, 0.0)
        accounts_list.append({
            "id": a.id,
            "name": a.name,
            "rsn": a.rsn,
            "starting_gp": a.starting_gp,
            "current_gp": a.starting_gp + pnl,
            "role": a.role,
            "active": a.active
        })

    # 2. Recommendations
    recs = session.query(Recommendation).order_by(Recommendation.created_at.desc()).limit(150).all()
    item_ids = [r.item_id for r in recs if r.item_id is not None]
    items = {i.id: i.name for i in session.query(Item.id, Item.name).filter(Item.id.in_(item_ids)).all()}

    rec_list = []
    for r in recs:
        name = items.get(r.item_id, "N/A") if r.item_id else "Skilling Recipe"
        expected_ret = f"{r.expected_return_pct * 100:.2f}%" if r.expected_return_pct else "N/A"
        rec_list.append({
            "id": r.id,
            "created_at": r.created_at.strftime("%H:%M:%S (%b %d)"),
            "strategy": r.strategy_name.replace("_processing", "").title(),
            "item": name,
            "side": r.side or "N/A",
            "qty": f"{r.qty:,.0f}" if r.qty else "N/A",
            "price_each": f"{r.price_each:,.0f} gp" if r.price_each else "N/A",
            "expected_profit_gp": f"+{r.expected_profit_gp:,.0f} gp" if r.expected_profit_gp else "N/A",
            "expected_return_pct": expected_ret,
            "taken": r.taken_trade_id is not None,
            "skipped": r.skipped,
            "reason": r.reason or "Margin Flip Setup"
        })

    # 3. Portfolio Open Positions
    positions = load_open_positions()
    portfolio_list = []
    n_positions = 0
    total_market_value = 0.0
    total_unrealized_pnl = 0.0

    if not positions.empty:
        marked = mark_to_market(positions, timestep="24h")
        summary = summarize_portfolio(marked)
        n_positions = summary["n_positions"]
        total_market_value = summary["total_market_value"]
        total_unrealized_pnl = summary["total_unrealized_pnl"]

        for _, row in marked.iterrows():
            portfolio_list.append({
                "account": row["account_name"],
                "item_id": row["item_id"],
                "item_name": row["item_name"],
                "qty": f"{row['net_qty']:,.0f}",
                "avg_cost": f"{row['avg_cost']:,.0f} gp",
                "mark_price": f"{row['mark_price']:,.0f} gp",
                "market_value": f"{row['market_value']:,.0f} gp",
                "unrealized_pnl": float(row["unrealized_pnl"]),
                "pnl_pct": float(row["pnl_pct"]) if not pd.isna(row["pnl_pct"]) else 0.0
            })

    # 4. Daemon loop heartbeat checks
    latest_rec_ts = session.query(func.max(Recommendation.created_at)).scalar()
    
    daemon_last_run = None
    daemon_running = False
    
    if latest_rec_ts:
        daemon_last_run = latest_rec_ts.strftime("%H:%M:%S (%b %d)")
        daemon_interval = settings.get("daemon", {}).get("interval_minutes", 5)
        cutoff = datetime.utcnow() - timedelta(minutes=daemon_interval + 1)
        daemon_running = latest_rec_ts >= cutoff

    session.close()

    return jsonify({
        "metrics": {
            "n_positions": n_positions,
            "total_market_value": total_market_value,
            "total_unrealized_pnl": total_unrealized_pnl
        },
        "accounts": accounts_list,
        "recommendations": rec_list,
        "portfolio": portfolio_list,
        "daemon_last_run": daemon_last_run,
        "daemon_running": daemon_running,
        "daemon_timestep": settings.get("ge", {}).get("default_timestep", "5m")
    })

@app.route("/api/items")
def get_items_list():
    session = get_session()
    items = session.query(Item).filter(Item.tradeable == True).order_by(Item.name).all()
    session.close()
    return jsonify([{"id": it.id, "name": it.name} for it in items])

@app.route("/api/item-detail/<int:item_id>")
def get_item_detail(item_id):
    session = get_session()
    item = session.query(Item).filter_by(id=item_id).first()
    if not item:
        session.close()
        return jsonify({"error": "Item not found"}), 404

    # Fetch daily WeirdGloop timeseries
    prices = (
        session.query(PricePoint)
        .filter(PricePoint.item_id == item_id, PricePoint.timestep == "1d_weirdgloop")
        .order_by(PricePoint.ts)
        .all()
    )
    session.close()

    history_points = []
    latest_high = 0.0
    latest_low = 0.0
    margin = 0.0

    if prices:
        df = pd.DataFrame([
            {
                "ts": p.ts.strftime("%Y-%m-%d"),
                "avg_high": p.avg_high,
                "avg_low": p.avg_low,
                "high_vol": p.high_vol or 0,
                "price": (p.avg_high + p.avg_low) / 2.0 if p.avg_high and p.avg_low else (p.avg_high or p.avg_low or 0.0)
            }
            for p in prices
        ]).dropna(subset=["price"])

        history_points = df.to_dict("records")
        if not df.empty:
            latest = df.iloc[-1]
            latest_high = latest["avg_high"] or 0.0
            latest_low = latest["avg_low"] or 0.0
            net_sell = latest_high - calculate_osrs_tax(latest_high)
            margin = net_sell - latest_low

    return jsonify({
        "id": item.id,
        "name": item.name,
        "examine": item.examine or "No description.",
        "limit": item.limit or 0,
        "members": item.members or False,
        "latest_high": latest_high,
        "latest_low": latest_low,
        "margin": margin,
        "history": history_points
    })

@app.route("/api/trades", methods=["POST"])
def post_trade():
    data = request.json or {}
    session = get_session()
    try:
        account_id = data.get("account_id")
        item_id = data.get("item_id")
        item_name = data.get("item_name", "Unknown Item")
        side = data.get("side", "buy")
        qty = int(data.get("qty", 0))
        price = int(data.get("price", 0))
        note = data.get("note", "")

        t = Trade(
            ts=datetime.utcnow(),
            account_id=account_id,
            item_id=item_id or 0,
            item_name=item_name,
            side=side,
            qty=qty,
            price_each=price,
            note=note
        )
        session.add(t)
        session.flush()

        rec_id = data.get("rec_id")
        if rec_id:
            rec = session.get(Recommendation, rec_id)
            if rec:
                rec.taken_trade_id = t.id

        session.commit()
        return jsonify({"success": True, "trade_id": t.id})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        session.close()

@app.route("/api/recommendations/<int:rec_id>/skip", methods=["POST"])
def skip_recommendation(rec_id):
    session = get_session()
    rec = session.get(Recommendation, rec_id)
    if not rec:
        session.close()
        return jsonify({"error": "Recommendation not found"}), 404

    try:
        rec.skipped = True
        session.commit()
        return jsonify({"success": True})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        session.close()

@app.route("/api/accounts", methods=["POST"])
def post_account():
    data = request.json or {}
    session = get_session()
    try:
        acc_id = data.get("id")
        name = data.get("name")
        rsn = data.get("rsn")
        starting_gp = int(data.get("starting_gp", 0))
        role = data.get("role", "core_liquidity")
        active = bool(data.get("active", True))

        if acc_id:
            a = session.get(Account, acc_id)
            if a:
                a.name = name
                a.rsn = rsn
                a.starting_gp = starting_gp
                a.role = role
                a.active = active
        else:
            a = Account(name=name, rsn=rsn, starting_gp=starting_gp, role=role, active=active)
            session.add(a)

        session.commit()
        return jsonify({"success": True})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        session.close()

@app.route("/api/news")
def get_news_impacts():
    session = get_session()
    impacts = (
        session.query(NewsImpact, NewsPost)
        .join(NewsPost, NewsImpact.news_post_id == NewsPost.id)
        .order_by(NewsPost.date.desc())
        .limit(40)
        .all()
    )
    session.close()

    rows = []
    for ni, np in impacts:
        source_type = np.category if np.category else "news"
        if ni.item_name_keywords == "none":
            continue
            
        rows.append({
            "date": np.date.strftime("%Y-%m-%d"),
            "title": np.title,
            "source": source_type,
            "items": ni.item_name_keywords,
            "direction": ni.direction.upper(),
            "confidence": f"{ni.confidence * 100:.0f}%",
            "expected_move": f"{'+' if ni.direction == 'up' else '-'}{ni.expected_move_pct * 100:.1f}%",
            "reasoning": ni.reasoning,
            "summary": np.summary or ""
        })
    return jsonify(rows)


@app.route("/api/runelite/sync", methods=["POST"])
def runelite_sync():
    data = request.json or {}
    rsn = data.get("rsn")
    if not rsn:
        return jsonify({"error": "RSN is required"}), 400

    session = get_session()
    try:
        # 1. Resolve Account
        account = session.query(Account).filter_by(rsn=rsn).first()
        if not account:
            # Create a default account if not found
            account = Account(
                name=rsn,
                rsn=rsn,
                starting_gp=100000000, # 100M default
                role="core_liquidity",
                active=True
            )
            session.add(account)
            session.flush()

        # 2. Record AccountBalance (cash_stack or bank_value)
        cash_stack = int(data.get("cash_stack", 0))
        bank_value = int(data.get("bank_value", 0))
        
        # Save a balance point
        from ..models import AccountBalance
        ab = AccountBalance(
            account_id=account.id,
            ts=datetime.utcnow(),
            gp=bank_value if bank_value > 0 else cash_stack
        )
        session.add(ab)

        # 3. Inject synced skills into hiscores cache to prevent hiscores scraping
        skills = data.get("skills", {})
        if skills:
            from ..hiscores import _HISCORES_CACHE
            import time
            skills_dict = {}
            for sname, lvl in skills.items():
                skills_dict[sname] = {"rank": -1, "level": int(lvl), "xp": 0}
            _HISCORES_CACHE[rsn] = (time.time(), skills_dict)

        session.commit()

        # 4. Fetch the latest active recommendations
        recs = session.query(Recommendation).order_by(Recommendation.created_at.desc()).limit(150).all()
        
        # Exclude duplicate items, sort by profit
        flips_dict = {}
        processing_list = []
        
        # Get all item mappings in one query to be efficient
        item_ids = [r.item_id for r in recs if r.item_id is not None]
        items_map = {}
        if item_ids:
            items_query = session.query(Item).filter(Item.id.in_(item_ids)).all()
            items_map = {it.id: it.name for it in items_query}

        for r in recs:
            if r.signal_type == "pure_flip" and not r.skipped and not r.taken_trade_id:
                if r.item_id not in flips_dict:
                    name = items_map.get(r.item_id, "Unknown Item")
                    margin = int(r.expected_profit_gp / r.qty) if r.qty else 0
                    flips_dict[r.item_id] = {
                        "name": name,
                        "id": r.item_id,
                        "buy_price": r.price_each or 0,
                        "margin": margin,
                        "expected_profit": r.expected_profit_gp or 0.0,
                        "limit": r.qty or 0
                    }
            elif r.signal_type == "processing":
                recipe_name = r.reason.split(" (Eligible:")[0] if r.reason else "Recipe"
                # Only suggest if this player is in the eligible list
                if "Eligible:" in (r.reason or ""):
                    eligible_str = r.reason.split("Eligible: ")[1].rstrip(")")
                    eligible_players = [p.strip().lower() for p in eligible_str.split(",")]
                    if rsn.lower() not in eligible_players:
                        continue
                
                processing_list.append({
                    "recipe": recipe_name,
                    "skill": r.strategy_name.replace("_processing", "").title(),
                    "level": r.price_each or 1,
                    "gp_per_batch": r.expected_profit_gp or 0.0
                })

        # Sort flips by profit descending, limit to 20
        sorted_flips = sorted(flips_dict.values(), key=lambda x: x["expected_profit"], reverse=True)[:20]
        # Sort processing by profit descending, limit to 20
        sorted_processing = sorted(processing_list, key=lambda x: x["gp_per_batch"], reverse=True)[:20]

        return jsonify({
            "success": True,
            "recommendations": {
                "flips": sorted_flips,
                "processing": sorted_processing
            }
        })
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/runelite/trade-event", methods=["POST"])
def runelite_trade_event():
    data = request.json or {}
    rsn = data.get("rsn")
    if not rsn:
        return jsonify({"error": "RSN is required"}), 400

    session = get_session()
    try:
        account = session.query(Account).filter_by(rsn=rsn).first()
        if not account:
            account = Account(
                name=rsn,
                rsn=rsn,
                starting_gp=100000000,
                role="core_liquidity",
                active=True
            )
            session.add(account)
            session.flush()

        item_id = int(data.get("item_id", 0))
        item_name = data.get("item_name", "Unknown Item")
        qty = int(data.get("qty", 0))
        price_each = int(data.get("price_each", 0))
        side = data.get("side", "buy")
        slot = data.get("slot", 0)

        t = Trade(
            ts=datetime.utcnow(),
            account_id=account.id,
            item_id=item_id,
            item_name=item_name,
            side=side,
            qty=qty,
            price_each=price_each,
            note=f"Logged via RuneLite Plugin (Slot {slot})"
        )
        session.add(t)
        session.commit()
        return jsonify({"success": True, "trade_id": t.id})
    except Exception as e:
        session.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        session.close()


@app.route("/api/settings")
def get_system_settings():
    settings = load_settings()
    strategies = load_strategies()
    
    high_margin_flip = {}
    for s in strategies.get("strategies", []):
        if s.get("name") == "high_margin_flip":
            high_margin_flip = s.get("params", {})
            break

    return jsonify({
        "ge": settings.get("ge", {}),
        "notifications": settings.get("notifications", {}),
        "daemon": settings.get("daemon", {}),
        "blacklist": settings.get("analysis", {}).get("blacklisted_items", []),
        "strategy": high_margin_flip
    })

@app.route("/api/settings", methods=["POST"])
def post_system_settings():
    data = request.json or {}
    try:
        # 1. Update settings.yaml configurations
        settings = load_settings()
        
        if "default_timestep" in data:
            settings.setdefault("ge", {})["default_timestep"] = data["default_timestep"]
            
        if "notifications_enabled" in data:
            settings.setdefault("notifications", {})["enabled"] = bool(data["notifications_enabled"])
        if "discord_webhook_url" in data:
            settings.setdefault("notifications", {})["discord_webhook_url"] = data["discord_webhook_url"]
        if "recipient_email" in data:
            settings.setdefault("notifications", {})["recipient_email"] = data["recipient_email"]
        if "smtp_server" in data:
            settings.setdefault("notifications", {})["smtp_server"] = data["smtp_server"]
        if "smtp_port" in data:
            settings.setdefault("notifications", {})["smtp_port"] = int(data["smtp_port"])
            
        if "min_profit_hot_alert_gp" in data:
            settings.setdefault("daemon", {})["min_profit_hot_alert_gp"] = int(data["min_profit_hot_alert_gp"])
        if "min_return_hot_alert_pct" in data:
            settings.setdefault("daemon", {})["min_return_hot_alert_pct"] = float(data["min_return_hot_alert_pct"])
        if "anti_spam_hours" in data:
            settings.setdefault("daemon", {})["anti_spam_hours"] = int(data["anti_spam_hours"])
            
        if "blacklist" in data:
            settings.setdefault("analysis", {})["blacklisted_items"] = data["blacklist"]
            
        save_settings(settings)
        
        # 2. Update strategies.yaml configurations
        strategies = load_strategies()
        for s in strategies.setdefault("strategies", []):
            if s.get("name") == "high_margin_flip":
                params = s.setdefault("params", {})
                if "min_margin_gp" in data:
                    params["min_margin_gp"] = int(data["min_margin_gp"])
                if "min_daily_volume" in data:
                    params["min_daily_volume"] = int(data["min_daily_volume"])
                if "max_spread_pct" in data:
                    params["max_spread_pct"] = int(data["max_spread_pct"])
                break
        save_strategies(strategies)
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/daemon/run-cycle", methods=["POST"])
def run_daemon_cycle():
    from ..engine import run_full_cycle
    if not daemon_lock.acquire(blocking=False):
        return jsonify({"error": "An analysis cycle is already in progress. Please wait."}), 409
    
    def run_in_thread():
        try:
            run_full_cycle(send_digest=True)
        finally:
            daemon_lock.release()
            
    try:
        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()
        return jsonify({"success": True})
    except Exception as e:
        daemon_lock.release()
        return jsonify({"error": str(e)}), 500

@app.route("/api/backtest", methods=["POST"])
def post_backtest():
    data = request.json or {}
    try:
        years = int(data.get("years", 1))
        timestep = data.get("timestep", "1d_weirdgloop")
        initial_capital = int(data.get("initial_capital", 100000000))
        k_std = float(data.get("k_std", 1.0))
        position_fraction = float(data.get("position_fraction", 0.05))
        fee_rate = float(data.get("fee_rate", 0.01))
        top_n = int(data.get("top_n", 300))

        res = backtest_flip_strategy(
            years=years,
            timestep=timestep,
            initial_capital=initial_capital,
            k_std=k_std,
            position_fraction=position_fraction,
            fee_rate=fee_rate,
            top_n=top_n
        )

        if "error" in res:
            return jsonify({"error": res["error"]}), 400

        metrics = res["metrics"]
        equity_curve = res.get("equity_curve", [])

        curve_data = []
        if equity_curve:
            eq_df = pd.DataFrame(equity_curve)
            eq_df["ts"] = eq_df["ts"].dt.strftime("%Y-%m-%d")
            curve_data = eq_df.to_dict("records")

        return jsonify({
            "metrics": {
                "final_equity": float(metrics.get("final_equity", 0.0)),
                "total_return": float(metrics.get("total_return", 0.0)),
                "sharpe": float(metrics.get("sharpe", 0.0)),
                "max_drawdown": float(metrics.get("max_drawdown", 0.0))
            },
            "config": res.get("config", {}),
            "equity_curve": curve_data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# --- Dashboard Runner ---

def run_background_daemon_loop():
    import time
    from ..news import fetch_news_archive, fetch_news_details
    from ..reddit import scrape_reddit
    from ..news_analyzer import analyze_unprocessed_news
    from ..engine import run_full_cycle
    from ..config import load_settings

    print("[Background Daemon] Continuous day-trading sentinel loop started.")
    last_digest_time = None

    while True:
        try:
            settings = load_settings()
            daemon_settings = settings.get("daemon", {})
            interval_mins = daemon_settings.get("interval_minutes", 5)

            start_time = time.time()
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"[Background Daemon] [{now_str}] Starting cycle update...")

            # 1. Fetch and analyze OSRS news, Reddit, and YouTube updates
            print("[Background Daemon] Fetching OSRS news updates...")
            try:
                posts = fetch_news_archive()
                for p in posts:
                    fetch_news_details(p)
            except Exception as e:
                print(f"[Background Daemon] [News Error] {e}")

            print("[Background Daemon] Fetching YouTube updates...")
            try:
                from ..news import fetch_youtube_feed
                fetch_youtube_feed()
            except Exception as e:
                print(f"[Background Daemon] [YouTube Error] {e}")

            print("[Background Daemon] Scraping r/2007scape Reddit discussions...")
            try:
                scrape_reddit()
            except Exception as e:
                print(f"[Background Daemon] [Reddit Error] {e}")

            print("[Background Daemon] Running sentiment analysis on news & Reddit posts...")
            try:
                analyze_unprocessed_news()
            except Exception as e:
                print(f"[Background Daemon] [Sentiment Error] {e}")

            # 2. Run price cycle & hot flip checks
            # Send digest on first run or every 12 hours
            should_send_digest = (last_digest_time is None or (time.time() - last_digest_time) >= 12 * 3600)
            print(f"[Background Daemon] Running full analysis cycle (send_digest={should_send_digest})...")
            with daemon_lock:
                run_full_cycle(send_digest=should_send_digest)
            if should_send_digest:
                last_digest_time = time.time()

            print(f"[Background Daemon] [{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] Cycle completed successfully.")

            elapsed = time.time() - start_time
            sleep_time = max(0.0, (interval_mins * 60.0) - elapsed)
            print(f"[Background Daemon] Sleeping for {sleep_time / 60.0:.2f} minutes until next update.")
            time.sleep(sleep_time)

        except Exception as e:
            import traceback
            print(f"[Background Daemon] [Error] Exception in cycle execution: {e}")
            traceback.print_exc()
            time.sleep(60) # Sleep 1 minute on major outer exception before retrying

def run_dashboard():
    settings = load_settings()
    host = settings["dashboard"].get("host", "127.0.0.1")
    port = settings["dashboard"].get("port", 8050)
    debug = settings["dashboard"].get("debug", False)

    # Start background daemon sentinel thread
    import os
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print("[Flask] Starting background Day-Trading Daemon thread...")
        daemon_thread = threading.Thread(target=run_background_daemon_loop, daemon=True)
        daemon_thread.start()

    print(f"[Flask] Running OSRS Bloomberg Terminal at http://{host}:{port}")
    app.run(host=host, port=port, debug=debug, threaded=True)
