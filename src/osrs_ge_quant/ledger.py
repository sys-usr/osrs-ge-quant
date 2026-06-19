# src/osrs_ge_quant/ledger.py
from datetime import datetime, timedelta
from typing import Dict, List, Any
import pandas as pd
from sqlalchemy import select, func

from .db import get_session
from .models import Account, AccountBalance, Trade, Item, PricePoint

def get_latest_gp_balances(session) -> Dict[int, int]:
    """
    Returns a mapping of account_id -> current GP balance.
    """
    balances = {}
    accounts = session.query(Account).filter_by(active=True).all()
    for acc in accounts:
        latest_bal = (
            session.query(AccountBalance)
            .filter_by(account_id=acc.id)
            .order_by(AccountBalance.ts.desc())
            .first()
        )
        balances[acc.id] = latest_bal.gp if latest_bal else acc.starting_gp
    return balances

def get_item_prices(session) -> Dict[int, float]:
    """
    Returns a mapping of item_id -> current average price.
    """
    latest_prices = {}
    subquery = (
        session.query(PricePoint.item_id, func.max(PricePoint.ts).label("max_ts"))
        .group_by(PricePoint.item_id)
        .subquery()
    )
    
    pps = (
        session.query(PricePoint)
        .join(subquery, (PricePoint.item_id == subquery.c.item_id) & (PricePoint.ts == subquery.c.max_ts))
        .all()
    )
    for p in pps:
        if p.avg_high and p.avg_low:
            latest_prices[p.item_id] = (p.avg_high + p.avg_low) / 2.0
        else:
            latest_prices[p.item_id] = p.avg_high or p.avg_low or 0.0
    return latest_prices

def get_consolidated_ledger() -> Dict[str, Any]:
    """
    Calculates consolidated balance sheet across all active alts:
    - Coins balance per account and sum
    - Holdings per account (qty, avg cost, current price, market value, unrealized PnL)
    - Total MV, Total Cash, Total Net Worth
    """
    session = get_session()
    gp_balances = get_latest_gp_balances(session)
    item_prices = get_item_prices(session)
    
    # Load all trades to reconstruct holdings
    trades = session.query(Trade).order_by(Trade.ts.asc()).all()
    
    # Calculate holdings: holdings[account_id][item_id] = {'qty': int, 'total_cost': float}
    holdings = {}
    account_names = {}
    
    accounts = session.query(Account).all()
    for acc in accounts:
        holdings[acc.id] = {}
        account_names[acc.id] = acc.name
        
    for t in trades:
        acc_id = t.account_id
        item_id = t.item_id
        
        if acc_id not in holdings:
            holdings[acc_id] = {}
            
        if item_id not in holdings[acc_id]:
            holdings[acc_id][item_id] = {"qty": 0, "total_cost": 0.0}
            
        if t.side == "buy":
            holdings[acc_id][item_id]["qty"] += t.qty
            holdings[acc_id][item_id]["total_cost"] += t.qty * t.price_each
        elif t.side == "sell":
            # Realized cost adjustment
            current_qty = holdings[acc_id][item_id]["qty"]
            if current_qty > 0:
                avg_cost = holdings[acc_id][item_id]["total_cost"] / current_qty
                holdings[acc_id][item_id]["qty"] = max(0, current_qty - t.qty)
                holdings[acc_id][item_id]["total_cost"] = holdings[acc_id][item_id]["qty"] * avg_cost

    # Format output tables
    consolidated_holdings = []
    total_holdings_mv = 0.0
    
    for acc_id, items_hold in holdings.items():
        acc_name = account_names.get(acc_id, f"Unknown (ID: {acc_id})")
        for item_id, h in items_hold.items():
            qty = h["qty"]
            if qty <= 0:
                continue
                
            avg_cost = h["total_cost"] / qty
            current_price = item_prices.get(item_id, avg_cost)
            mv = qty * current_price
            unrealized_pnl = mv - h["total_cost"]
            
            total_holdings_mv += mv
            
            # Get item name
            item_obj = session.get(Item, item_id)
            item_name = item_obj.name if item_obj else f"Item {item_id}"
            
            consolidated_holdings.append({
                "account_id": acc_id,
                "account_name": acc_name,
                "item_id": item_id,
                "item_name": item_name,
                "qty": qty,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "market_value": mv,
                "unrealized_pnl": unrealized_pnl
            })
            
    total_cash = sum(gp_balances.values())
    total_net_worth = total_cash + total_holdings_mv
    
    accounts_summary = []
    for acc_id, cash in gp_balances.items():
        acc_name = account_names.get(acc_id, f"Account {acc_id}")
        acc_mv = sum(h["market_value"] for h in consolidated_holdings if h["account_id"] == acc_id)
        accounts_summary.append({
            "account_id": acc_id,
            "account_name": acc_name,
            "cash": cash,
            "holdings_value": acc_mv,
            "net_worth": cash + acc_mv
        })
        
    session.close()
    
    return {
        "accounts": accounts_summary,
        "holdings": consolidated_holdings,
        "total_cash": total_cash,
        "total_holdings_value": total_holdings_mv,
        "total_net_worth": total_net_worth
    }

def get_account_buy_volume_4h(session, account_id: int, item_id: int) -> int:
    """
    Returns the total volume of item_id bought by account_id in the last 4 hours.
    """
    time_limit = datetime.utcnow() - timedelta(hours=4)
    res = (
        session.query(func.sum(Trade.qty))
        .filter(
            Trade.account_id == account_id,
            Trade.item_id == item_id,
            Trade.side == "buy",
            Trade.ts >= time_limit
        )
        .scalar()
    )
    return int(res) if res else 0

def allocate_buy_order(item_id: int, qty: int) -> Dict[str, Any] or None:
    """
    Finds the active account that has the highest remaining buy capacity for this item.
    Caps the allocated qty if total capacity is lower than requested.
    """
    session = get_session()
    item = session.get(Item, item_id)
    if not item:
        session.close()
        return None
        
    limit = item.limit if item.limit and item.limit > 0 else 10000
    
    active_accounts = session.query(Account).filter_by(active=True).all()
    if not active_accounts:
        session.close()
        return None
        
    allocation_candidates = []
    for acc in active_accounts:
        bought_vol = get_account_buy_volume_4h(session, acc.id, item_id)
        remaining = max(0, limit - bought_vol)
        
        # Check cash balance
        latest_bal = (
            session.query(AccountBalance)
            .filter_by(account_id=acc.id)
            .order_by(AccountBalance.ts.desc())
            .first()
        )
        cash = latest_bal.gp if latest_bal else acc.starting_gp
        
        allocation_candidates.append({
            "account_id": acc.id,
            "account_name": acc.name,
            "remaining_limit": remaining,
            "cash": cash
        })
        
    session.close()
    
    # Sort candidate accounts by remaining limit desc, then by cash balance desc
    allocation_candidates.sort(key=lambda x: (x["remaining_limit"], x["cash"]), reverse=True)
    best_candidate = allocation_candidates[0]
    
    if best_candidate["remaining_limit"] <= 0:
        print(f"[Ledger] Warning: All accounts have hit 4h buy limits for item {item.name}.")
        return None
        
    allocated_qty = min(qty, best_candidate["remaining_limit"])
    
    return {
        "account_id": best_candidate["account_id"],
        "account_name": best_candidate["account_name"],
        "allocated_qty": allocated_qty,
        "remaining_limit": best_candidate["remaining_limit"]
    }


def optimize_capital_allocation(opportunities: List[Dict[str, Any]], total_capital: float = None) -> List[Dict[str, Any]]:
    """
    Optimize capital allocation across active accounts for given opportunities using scipy.optimize.linprog.
    
    Each opportunity in opportunities should be a dict:
      - item_id: int
      - price_each: int
      - expected_return_pct: float
      - qty: int (recommended quantity, default to item limit if not provided)
      
    Constraints:
      - Non-negativity: x_{i, j} >= 0
      - Cash capacity per account: sum_i x_{i, j} * price_each_i <= cash_j
      - Total capital cap (if total_capital is provided): sum_i,j x_{i, j} * price_each_i <= total_capital
      - 4-hour buy limits: x_{i, j} <= remaining_limit_{i, j}
      - Recommended quantity cap: sum_j x_{i, j} <= qty_i
      
    Objective:
      - Maximize total expected profit: sum_i,j x_{i, j} * expected_profit_each_i
    """
    from scipy.optimize import linprog
    import numpy as np
    
    session = get_session()
    
    # 1. Retrieve active accounts and their current balances
    active_accounts = session.query(Account).filter_by(active=True).all()
    if not active_accounts or not opportunities:
        session.close()
        return []
        
    num_opps = len(opportunities)
    num_accs = len(active_accounts)
    
    acc_cash = {}
    for acc in active_accounts:
        latest_bal = (
            session.query(AccountBalance)
            .filter_by(account_id=acc.id)
            .order_by(AccountBalance.ts.desc())
            .first()
        )
        acc_cash[acc.id] = latest_bal.gp if latest_bal else acc.starting_gp

    # 2. Build coefficients for the objective function (linprog minimizes, so we negate expected profit)
    # Variables are x_{i, j} where i is opportunity index, j is account index.
    # We flatten to a 1D array of size num_opps * num_accs.
    # Vector index = i * num_accs + j
    c = []
    bounds = []
    
    for i, opp in enumerate(opportunities):
        item_id = opp["item_id"]
        price_each = opp["price_each"]
        ret_pct = opp.get("expected_return_pct", 0.0)
        expected_profit_each = price_each * ret_pct
        
        # Query item for 4-hour limit
        item = session.get(Item, item_id)
        limit = item.limit if item and item.limit and item.limit > 0 else 10000
        
        for j, acc in enumerate(active_accounts):
            c.append(-expected_profit_each)
            
            # Query remaining buy limit for this account and item
            bought_vol = get_account_buy_volume_4h(session, acc.id, item_id)
            remaining_limit = max(0, limit - bought_vol)
            
            # The upper bound for this specific account-item pair is remaining_limit
            bounds.append((0, remaining_limit))
            
    c = np.array(c)
    
    # 3. Build Inequality Matrix A_ub and Vector b_ub
    A_ub = []
    b_ub = []
    
    # Constraint A: Cash capacity per account j: sum_i x_{i, j} * price_each_i <= cash_j
    for j, acc in enumerate(active_accounts):
        row = np.zeros(num_opps * num_accs)
        for i, opp in enumerate(opportunities):
            row[i * num_accs + j] = opp["price_each"]
        A_ub.append(row)
        b_ub.append(acc_cash[acc.id])
        
    # Constraint B: Recommended quantity cap per opportunity i: sum_j x_{i, j} <= qty_i
    # We bypass individual account limits by allowing the total order to scale up to the consolidated limit of all active alts.
    for i, opp in enumerate(opportunities):
        row = np.zeros(num_opps * num_accs)
        qty_cap = opp.get("qty")
        if qty_cap is None or qty_cap <= 0:
            item = session.get(Item, opp["item_id"])
            limit_val = item.limit if item and item.limit and item.limit > 0 else 10000
            qty_cap = limit_val * num_accs
            
        for j in range(num_accs):
            row[i * num_accs + j] = 1.0
        A_ub.append(row)
        b_ub.append(qty_cap)
        
    # Constraint C: Total capital cap (if total_capital is provided)
    if total_capital is not None and total_capital > 0:
        row = np.zeros(num_opps * num_accs)
        for i, opp in enumerate(opportunities):
            for j in range(num_accs):
                row[i * num_accs + j] = opp["price_each"]
        A_ub.append(row)
        b_ub.append(total_capital)
        
    A_ub = np.array(A_ub)
    b_ub = np.array(b_ub)
    
    session.close()
    
    # 4. Run linear programming solver
    res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
    
    allocations = []
    if res.success:
        x = res.x
        for i, opp in enumerate(opportunities):
            item_id = opp["item_id"]
            price_each = opp["price_each"]
            ret_pct = opp.get("expected_return_pct", 0.0)
            
            # Fetch item name for readability
            session_temp = get_session()
            item_obj = session_temp.get(Item, item_id)
            item_name = item_obj.name if item_obj else f"Item {item_id}"
            session_temp.close()
            
            for j, acc in enumerate(active_accounts):
                val = x[i * num_accs + j]
                allocated_qty = int(np.floor(val + 1e-9))
                if allocated_qty > 0:
                    expected_profit = allocated_qty * price_each * ret_pct
                    allocations.append({
                        "account_id": acc.id,
                        "account_name": acc.name,
                        "item_id": item_id,
                        "item_name": item_name,
                        "price_each": price_each,
                        "allocated_qty": allocated_qty,
                        "expected_profit": expected_profit
                    })
    else:
        print(f"[Ledger] Capital allocation optimization failed: {res.message}")
        
    return allocations


def inspect_capital_opportunity_cost(stagnant_hours: float = 24.0, benchmark_yield_pct_per_day: float = 1.0) -> List[Dict[str, Any]]:
    """
    Capital opportunity cost inspector.
    Compares asset yields (unrealized PnL or time open) against a benchmark liquidity return rate.
    Identifies and returns holdings that are underperforming or stagnant.
    """
    ledger = get_consolidated_ledger()
    holdings = ledger.get("holdings", [])
    
    underperforming = []
    session = get_session()
    
    now = datetime.utcnow()
    
    for h in holdings:
        item_id = h["item_id"]
        acc_id = h["account_id"]
        qty = h["qty"]
        avg_cost = h["avg_cost"]
        current_price = h["current_price"]
        market_value = h["market_value"]
        unrealized_pnl = h["unrealized_pnl"]
        
        # Query last buy trade for this item to determine holding period duration
        last_buy = (
            session.query(Trade)
            .filter(
                Trade.account_id == acc_id,
                Trade.item_id == item_id,
                Trade.side == "buy"
            )
            .order_by(Trade.ts.desc())
            .first()
        )
        
        if not last_buy:
            continue
            
        holding_time = now - last_buy.ts
        hours_held = holding_time.total_seconds() / 3600.0
        
        # Opportunity cost: if benchmark is R% per day, expected return for H hours is:
        # benchmark_yield_pct_per_day * (hours_held / 24.0)
        expected_return_pct = (benchmark_yield_pct_per_day / 100.0) * (hours_held / 24.0)
        expected_pnl = h["qty"] * avg_cost * expected_return_pct
        
        # Realized/Unrealized yield percentage
        actual_return_pct = unrealized_pnl / (h["qty"] * avg_cost) if avg_cost > 0 else 0.0
        
        is_stagnant = hours_held >= stagnant_hours
        is_underperforming = (unrealized_pnl < expected_pnl) or (actual_return_pct < expected_return_pct)
        
        if is_stagnant or is_underperforming:
            underperforming.append({
                "account_id": acc_id,
                "account_name": h["account_name"],
                "item_id": item_id,
                "item_name": h["item_name"],
                "qty": qty,
                "avg_cost": avg_cost,
                "current_price": current_price,
                "hours_held": hours_held,
                "unrealized_pnl": unrealized_pnl,
                "expected_pnl": expected_pnl,
                "opportunity_cost": expected_pnl - unrealized_pnl,
                "reason": "Stagnant (held > {}h)".format(stagnant_hours) if is_stagnant else "Underperforming benchmark yield"
            })
            
    session.close()
    # Sort by opportunity cost descending (highest cost first)
    underperforming.sort(key=lambda x: x["opportunity_cost"], reverse=True)
    return underperforming
