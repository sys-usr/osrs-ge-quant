# src/osrs_ge_quant/ml_predictor.py

import os
import re
from typing import Tuple, List, Dict, Any
import numpy as np
import pandas as pd
from sqlalchemy import func

try:
    import joblib
except ImportError:
    import pickle as joblib

from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier, GradientBoostingRegressor
import lightgbm as lgb

from .db import get_session
from .models import Recommendation, PricePoint, Item
from .config import load_settings, PROJECT_ROOT
from .strategy import calculate_osrs_tax

# Paths where trained models are saved
PROFIT_RF_PATH = os.path.join(str(PROJECT_ROOT), "data", "flip_profit_model.joblib") # RF regressor
PROFIT_LGBM_PATH = os.path.join(str(PROJECT_ROOT), "data", "flip_profit_lgbm.joblib") # LGBM regressor
DISCREPANCY_MODEL_PATH = os.path.join(str(PROJECT_ROOT), "data", "flip_discrepancy_model.joblib") # GB regressor
SUCCESS_RF_PATH = os.path.join(str(PROJECT_ROOT), "data", "flip_success_model.joblib") # RF classifier
SUCCESS_LGBM_PATH = os.path.join(str(PROJECT_ROOT), "data", "flip_success_lgbm.joblib") # LGBM classifier

def parse_indicators(reason_str: str) -> Tuple[float, float, float, float, float, float]:
    """
    Extract technical indicators from Recommendation.reason string:
    'RSI: 42.1 | BB: [1,200 - 1,450] | Vol: 1.5x | SpreadVol: 10.2 | VWAP: 1300.0'
    Returns (rsi, bb_lower, bb_upper, vol_surge, spread_vol, vwap)
    """
    rsi = 50.0
    bb_l = 0.0
    bb_u = 0.0
    vol = 1.0
    spread_vol = 0.0
    vwap = 0.0

    if not reason_str:
        return rsi, bb_l, bb_u, vol, spread_vol, vwap

    rsi_match = re.search(r"RSI:\s*([\d\.]+)", reason_str)
    if rsi_match:
        try:
            rsi = float(rsi_match.group(1))
        except ValueError:
            pass

    bb_match = re.search(r"BB:\s*\[\s*([\d,]+)\s*-\s*([\d,]+)\s*\]", reason_str)
    if bb_match:
        try:
            bb_l = float(bb_match.group(1).replace(",", ""))
            bb_u = float(bb_match.group(2).replace(",", ""))
        except ValueError:
            pass

    vol_match = re.search(r"Vol:\s*([\d\.]+)x", reason_str)
    if vol_match:
        try:
            vol = float(vol_match.group(1))
        except ValueError:
            pass

    sv_match = re.search(r"SpreadVol:\s*([\d\.]+)", reason_str)
    if sv_match:
        try:
            spread_vol = float(sv_match.group(1))
        except ValueError:
            pass

    vwap_match = re.search(r"VWAP:\s*([\d\.]+)", reason_str)
    if vwap_match:
        try:
            vwap = float(vwap_match.group(1))
        except ValueError:
            pass

    return rsi, bb_l, bb_u, vol, spread_vol, vwap

def build_historical_dataset() -> pd.DataFrame:
    """
    Fetch all pure_flip recommendations and match with subsequent price data
    to build a training dataset.
    """
    session = get_session()
    settings = load_settings()
    default_timestep = settings.get("ge", {}).get("default_timestep", "24h")

    recs = (
        session.query(Recommendation)
        .filter(Recommendation.signal_type == "pure_flip")
        .order_by(Recommendation.created_at.desc())
        .limit(10000)
        .all()
    )

    if not recs:
        session.close()
        return pd.DataFrame()

    recs.reverse()  # Restore chronological order

    item_ids = list(set(r.item_id for r in recs if r.item_id is not None))

    from datetime import timedelta
    min_ts = min(r.created_at for r in recs)
    max_ts = max(r.created_at for r in recs)

    # Fetch price points for resolving actual sell prices, bounded by the timestamp range of our recommendations
    price_points = (
        session.query(PricePoint.item_id, PricePoint.ts, PricePoint.avg_high, PricePoint.avg_low)
        .filter(
            PricePoint.item_id.in_(item_ids),
            PricePoint.timestep == default_timestep,
            PricePoint.ts >= min_ts - timedelta(hours=12),
            PricePoint.ts <= max_ts + timedelta(days=2)
        )
        .order_by(PricePoint.ts.asc())
        .all()
    )
    if not price_points:
        price_points = (
            session.query(PricePoint.item_id, PricePoint.ts, PricePoint.avg_high, PricePoint.avg_low)
            .filter(
                PricePoint.item_id.in_(item_ids),
                PricePoint.ts >= min_ts - timedelta(hours=12),
                PricePoint.ts <= max_ts + timedelta(days=2)
            )
            .order_by(PricePoint.ts.asc())
            .all()
        )
    session.close()

    # Group price points
    from collections import defaultdict
    prices_by_item = defaultdict(list)
    for pp in price_points:
        prices_by_item[pp.item_id].append({
            "ts": pp.ts,
            "avg_high": pp.avg_high,
            "avg_low": pp.avg_low
        })

    from datetime import timedelta

    rows = []
    for r in recs:
        if r.item_id is None or r.price_each is None or r.qty is None or not r.reason:
            continue

        buy_price = r.price_each
        qty = r.qty
        created_at = r.created_at

        # Match subsequent price
        pps = prices_by_item[r.item_id]
        sell_pp = None
        for pp in pps:
            if pp["ts"] > created_at and pp["avg_high"] is not None:
                sell_pp = pp
                break

        if not sell_pp:
            # Skip open trades for model training
            continue

        sell_price = sell_pp["avg_high"]
        tax = calculate_osrs_tax(sell_price)
        actual_profit = qty * (sell_price - tax - buy_price)
        expected_profit = r.expected_profit_gp or 0.0
        
        # Discrepancy (error) between actual and expected profit
        discrepancy = actual_profit - expected_profit

        # Extract technical indicator features
        rsi, bb_l, bb_u, vol_surge, spread_vol, vwap = parse_indicators(r.reason)

        bb_spread_pct = (bb_u - bb_l) / buy_price if buy_price > 0 else 0.0
        bb_position_pct = (buy_price - bb_l) / (bb_u - bb_l) if (bb_u > bb_l) else 0.5

        # Check if the trade successfully reached target margin within 4 hours
        reached = False
        valid_pps_4h = [pp for pp in pps if created_at < pp["ts"] <= created_at + timedelta(hours=4)]
        if not valid_pps_4h:
            valid_pps_4h = [sell_pp]
        for pp in valid_pps_4h:
            sh = pp["avg_high"]
            if sh is not None:
                sh_tax = calculate_osrs_tax(sh)
                target_net = expected_profit / qty if qty > 0 else 0.0
                if (sh - sh_tax - buy_price) >= target_net:
                    reached = True
                    break

        rows.append({
            "buy_price": buy_price,
            "qty": qty,
            "expected_profit": expected_profit,
            "expected_return_pct": r.expected_return_pct or 0.0,
            "rsi": rsi,
            "vol_surge": vol_surge,
            "bb_spread_pct": bb_spread_pct,
            "bb_position_pct": bb_position_pct,
            "spread_vol": spread_vol,
            "vwap": vwap,
            "actual_profit": actual_profit,
            "discrepancy": discrepancy,
            "is_success": 1 if reached else 0
        })

    return pd.DataFrame(rows)

def train_flip_model() -> Dict[str, Any]:
    """
    Train regressor and classifier models on historical flip details.
    Saves models to the data/ folder.
    """
    df = build_historical_dataset()
    if df.empty or len(df) < 10:
        return {"error": f"Insufficient completed trade history (got {len(df)} samples, need at least 10)."}

    # Define features and targets
    feature_cols = [
        "buy_price", "qty", "expected_profit", "expected_return_pct",
        "rsi", "vol_surge", "bb_spread_pct", "bb_position_pct",
        "spread_vol", "vwap"
    ]
    
    X = df[feature_cols].fillna(0.0)
    y_profit = df["actual_profit"]
    y_success = df["is_success"]
    y_discrepancy = df["discrepancy"]

    # Initialize and fit models
    print(f"[ML] Training ensemble models on {len(df)} samples...")
    
    # 1. Random Forest Regressor & Classifier
    reg_rf = RandomForestRegressor(n_estimators=100, random_state=42)
    reg_rf.fit(X, y_profit)
    clf_rf = RandomForestClassifier(n_estimators=100, random_state=42)
    clf_rf.fit(X, y_success)

    # 2. LightGBM Regressor & Classifier
    # Suppress verbose output of LightGBM
    reg_lgb = lgb.LGBMRegressor(n_estimators=100, random_state=42, verbose=-1)
    reg_lgb.fit(X, y_profit)
    clf_lgb = lgb.LGBMClassifier(n_estimators=100, random_state=42, verbose=-1)
    clf_lgb.fit(X, y_success)

    # 3. Gradient Boosting Regressor trained on Discrepancy (bias corrector)
    reg_disc = GradientBoostingRegressor(n_estimators=100, random_state=42)
    reg_disc.fit(X, y_discrepancy)

    # Make sure parent directory exists
    os.makedirs(os.path.dirname(PROFIT_RF_PATH), exist_ok=True)

    # Save models
    joblib.dump(reg_rf, PROFIT_RF_PATH)
    joblib.dump(reg_lgb, PROFIT_LGBM_PATH)
    joblib.dump(reg_disc, DISCREPANCY_MODEL_PATH)
    joblib.dump(clf_rf, SUCCESS_PATH := SUCCESS_RF_PATH)
    joblib.dump(clf_lgb, SUCCESS_LGBM_PATH)

    print(f"[ML] Ensemble Models successfully trained and saved under data/ folder.")

    # Compute in-sample predictions and metrics
    rf_preds = reg_rf.predict(X)
    lgb_preds = reg_lgb.predict(X)
    ensemble_preds = (rf_preds + lgb_preds) / 2.0
    
    # Correct with predicted discrepancy
    disc_preds = reg_disc.predict(X)
    adjusted_preds = df["expected_profit"] + disc_preds

    mae_ensemble = np.mean(np.abs(ensemble_preds - y_profit))
    mae_adjusted = np.mean(np.abs(adjusted_preds - y_profit))
    
    clf_rf_preds = clf_rf.predict(X)
    clf_lgb_preds = clf_lgb.predict(X)
    ensemble_clf_preds = (clf_rf_preds + clf_lgb_preds) >= 1
    accuracy = np.mean(ensemble_clf_preds == y_success)

    return {
        "success": True,
        "sample_count": len(df),
        "mae": float(mae_ensemble),
        "mae_adjusted": float(mae_adjusted),
        "accuracy": float(accuracy)
    }

def predict_live_flips(live_recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Inject ML predicted profits, success probability, and ratings into live recommendations.
    If models aren't trained yet, returns recommendations unchanged.
    """
    models_exist = (
        os.path.exists(PROFIT_RF_PATH) and
        os.path.exists(PROFIT_LGBM_PATH) and
        os.path.exists(DISCREPANCY_MODEL_PATH) and
        os.path.exists(SUCCESS_RF_PATH) and
        os.path.exists(SUCCESS_LGBM_PATH)
    )
    if not models_exist:
        return live_recs

    try:
        reg_rf = joblib.load(PROFIT_RF_PATH)
        reg_lgb = joblib.load(PROFIT_LGBM_PATH)
        reg_disc = joblib.load(DISCREPANCY_MODEL_PATH)
        clf_rf = joblib.load(SUCCESS_RF_PATH)
        clf_lgb = joblib.load(SUCCESS_LGBM_PATH)
    except Exception as e:
        print(f"[ML] Error loading saved models: {e}")
        return live_recs

    scored_recs = []
    for r in live_recs:
        # Parse indicator values from reason string
        reason_str = r.get("reason", "")
        parsed_rsi, parsed_bb_l, parsed_bb_u, parsed_vol, parsed_sv, parsed_vwap = parse_indicators(reason_str)

        # Convert formatted strings to numbers
        try:
            buy_price = int(str(r.get("price_each", "0")).replace(",", "").replace(" gp", "").strip())
        except Exception:
            buy_price = 0

        try:
            qty = int(str(r.get("qty", "0")).replace(",", "").strip())
        except Exception:
            qty = 0

        try:
            expected_profit = float(str(r.get("expected_profit_gp", "0")).replace(",", "").replace(" gp", "").replace("+", "").strip())
        except Exception:
            expected_profit = 0.0

        try:
            expected_ret = float(str(r.get("expected_return_pct", "0")).replace("%", "").strip()) / 100.0
        except Exception:
            expected_ret = 0.0

        spread_vol = r.get("spread_vol")
        vwap = r.get("vwap")
        if spread_vol is None:
            spread_vol = parsed_sv
        if vwap is None:
            vwap = parsed_vwap

        rsi = r.get("rsi", parsed_rsi)
        bb_l = r.get("bb_lower", parsed_bb_l)
        bb_u = r.get("bb_upper", parsed_bb_u)
        vol_surge = r.get("vol_surge", parsed_vol)

        bb_spread_pct = (bb_u - bb_l) / buy_price if buy_price > 0 else 0.0
        bb_position_pct = (buy_price - bb_l) / (bb_u - bb_l) if (bb_u > bb_l) else 0.5

        features = pd.DataFrame([{
            "buy_price": buy_price,
            "qty": qty,
            "expected_profit": expected_profit,
            "expected_return_pct": expected_ret,
            "rsi": rsi,
            "vol_surge": vol_surge,
            "bb_spread_pct": bb_spread_pct,
            "bb_position_pct": bb_position_pct,
            "spread_vol": spread_vol,
            "vwap": vwap
        }])

        try:
            # 1. Ensemble predicted profit
            pred_p_rf = float(reg_rf.predict(features)[0])
            pred_p_lgb = float(reg_lgb.predict(features)[0])
            ensemble_profit = (pred_p_rf + pred_p_lgb) / 2.0
            
            # 2. Bias correction via discrepancy model
            pred_discrepancy = float(reg_disc.predict(features)[0])
            adjusted_expected_profit = expected_profit + pred_discrepancy
            
            # Weighted combine (60% ensemble predicted, 40% bias-adjusted expected profit)
            final_pred_profit = 0.6 * ensemble_profit + 0.4 * adjusted_expected_profit

            # 3. Success probability
            prob_success_rf = float(clf_rf.predict_proba(features)[0][1])
            prob_success_lgb = float(clf_lgb.predict_proba(features)[0][1])
            prob_success = (prob_success_rf + prob_success_lgb) / 2.0
        except Exception as e:
            print(f"[ML] Error running inference: {e}")
            final_pred_profit = expected_profit
            prob_success = 0.5

        # Define ratings based on success probability
        if prob_success >= 0.80:
            rating = "STRONG BUY"
        elif prob_success >= 0.60:
            rating = "BUY"
        else:
            rating = "HOLD/NEUTRAL"

        # Attach ML fields to the recommendation
        r["ml_predicted_profit"] = f"+{final_pred_profit:,.0f} gp" if final_pred_profit >= 0 else f"-{abs(final_pred_profit):,.0f} gp"
        r["ml_success_probability"] = f"{prob_success * 100:.1f}%"
        r["ml_rating"] = rating

        scored_recs.append(r)

    # Sort scored recommendations so that STRONG BUY / high success probability bubbles to the top
    # This distills the absolute best item trades to make
    def sort_key(rec):
        try:
            prob = float(rec.get("ml_success_probability", "0%").replace("%", ""))
        except Exception:
            prob = 0.0
        try:
            profit = float(rec.get("ml_predicted_profit", "0").replace(",", "").replace(" gp", "").replace("+", "").strip())
        except Exception:
            profit = 0.0
        return (prob, profit)

    scored_recs.sort(key=sort_key, reverse=True)
    return scored_recs
