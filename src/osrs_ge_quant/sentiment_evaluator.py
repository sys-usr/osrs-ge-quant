# src/osrs_ge_quant/sentiment_evaluator.py

import os
from datetime import datetime, timedelta
from typing import Dict, List, Any
import numpy as np
import pandas as pd

from .db import get_session
from .models import NewsImpact, NewsPost, Item, PricePoint


def evaluate_sentiment_performance() -> Dict[str, Any]:
    """
    Evaluates historical NewsImpact predictions against subsequent real GE price moves
    (3 days and 7 days after the post).
    """
    session = get_session()

    # Query all impacts that aren't dummy/none
    impacts = (
        session.query(NewsImpact, NewsPost)
        .join(NewsPost, NewsImpact.news_post_id == NewsPost.id)
        .filter(NewsImpact.item_name_keywords != "none")
        .order_by(NewsPost.date.asc())
        .all()
    )

    if not impacts:
        session.close()
        return {}

    evals = []

    for ni, np_post in impacts:
        category = np_post.category if np_post.category else "news"
        post_date = np_post.date
        keywords = [kw.strip() for kw in ni.item_name_keywords.split(",") if kw.strip()]
        
        for kw in keywords:
            # 1. Resolve item
            item = session.query(Item).filter(Item.name.ilike(kw)).first()
            if not item:
                # Try partial match
                item = session.query(Item).filter(Item.name.ilike(f"%{kw}%")).first()
            
            if not item:
                continue

            # 2. Get price history for item (prefer 1d_weirdgloop for daily historical coverage)
            prices = (
                session.query(PricePoint.ts, PricePoint.avg_high, PricePoint.avg_low)
                .filter(PricePoint.item_id == item.id)
                .order_by(PricePoint.ts.asc())
                .all()
            )
            if not prices:
                continue

            df_p = pd.DataFrame([
                {
                    "ts": p[0],
                    "price": (p[1] + p[2]) / 2.0 if p[1] and p[2] else (p[1] or p[2] or 0.0)
                }
                for p in prices
            ]).dropna().sort_values("ts")

            if df_p.empty:
                continue

            # 3. Find price at post date (closest ts)
            df_p["time_diff"] = (df_p["ts"] - post_date).abs()
            closest_idx = df_p["time_diff"].idxmin()
            p0_row = df_p.loc[closest_idx]
            
            # Ensure price point is within 24 hours of the post date to be a valid baseline
            if p0_row["time_diff"] > timedelta(days=1):
                continue

            p0 = p0_row["price"]
            if p0 <= 0:
                continue

            # 4. Find price at 3 days and 7 days after
            t3 = post_date + timedelta(days=3)
            t7 = post_date + timedelta(days=7)

            # Closest price point to t3
            df_p["diff_3d"] = (df_p["ts"] - t3).abs()
            row_3d = df_p.loc[df_p["diff_3d"].idxmin()]
            
            # Closest price point to t7
            df_p["diff_7d"] = (df_p["ts"] - t7).abs()
            row_7d = df_p.loc[df_p["diff_7d"].idxmin()]

            # Only evaluate if price points are reasonably close to the target horizon (within 1.5 days)
            p3 = row_3d["price"] if row_3d["diff_3d"] <= timedelta(hours=36) else None
            p7 = row_7d["price"] if row_7d["diff_7d"] <= timedelta(hours=36) else None

            # Calculate actual moves
            move_3d = (p3 - p0) / p0 if p3 is not None else None
            move_7d = (p7 - p0) / p0 if p7 is not None else None

            # Evaluate matches
            match_3d = None
            if move_3d is not None:
                if ni.direction == "up" and move_3d > 0:
                    match_3d = 1
                elif ni.direction == "down" and move_3d < 0:
                    match_3d = 1
                else:
                    match_3d = 0

            match_7d = None
            if move_7d is not None:
                if ni.direction == "up" and move_7d > 0:
                    match_7d = 1
                elif ni.direction == "down" and move_7d < 0:
                    match_7d = 1
                else:
                    match_7d = 0

            evals.append({
                "category": category,
                "item_name": item.name,
                "post_title": np_post.title,
                "pred_direction": ni.direction,
                "confidence": ni.confidence,
                "expected_move": ni.expected_move_pct,
                "move_3d": move_3d,
                "move_7d": move_7d,
                "match_3d": match_3d,
                "match_7d": match_7d
            })

    session.close()

    if not evals:
        return {}

    df_evals = pd.DataFrame(evals)

    # Aggregate by category
    summary = {}
    for cat in ["news", "reddit", "youtube"]:
        df_sub = df_evals[df_evals["category"] == cat]
        if df_sub.empty:
            summary[cat] = {
                "count": 0,
                "accuracy_3d": 0.0,
                "accuracy_7d": 0.0,
                "mean_return_up_3d": 0.0,
                "mean_return_down_3d": 0.0,
                "mean_return_up_7d": 0.0,
                "mean_return_down_7d": 0.0
            }
            continue

        # Accuracy
        acc_3d = float(df_sub["match_3d"].mean()) if df_sub["match_3d"].notna().sum() > 0 else 0.0
        acc_7d = float(df_sub["match_7d"].mean()) if df_sub["match_7d"].notna().sum() > 0 else 0.0

        # Returns conditional on prediction
        up_3d = df_sub[df_sub["pred_direction"] == "up"]["move_3d"].dropna()
        down_3d = df_sub[df_sub["pred_direction"] == "down"]["move_3d"].dropna()
        up_7d = df_sub[df_sub["pred_direction"] == "up"]["move_7d"].dropna()
        down_7d = df_sub[df_sub["pred_direction"] == "down"]["move_7d"].dropna()

        summary[cat] = {
            "count": len(df_sub),
            "accuracy_3d": round(acc_3d * 100, 2),
            "accuracy_7d": round(acc_7d * 100, 2),
            "mean_return_up_3d": round(float(up_3d.mean() * 100), 2) if len(up_3d) > 0 else 0.0,
            "mean_return_down_3d": round(float(down_3d.mean() * 100), 2) if len(down_3d) > 0 else 0.0,
            "mean_return_up_7d": round(float(up_7d.mean() * 100), 2) if len(up_7d) > 0 else 0.0,
            "mean_return_down_7d": round(float(down_7d.mean() * 100), 2) if len(down_7d) > 0 else 0.0
        }

    return summary
