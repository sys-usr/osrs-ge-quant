# src/osrs_ge_quant/speculator.py
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any

from .db import get_session
from .models import NewsPost, NewsImpact, Item, PricePoint, Recommendation
from .news import fetch_news_archive, fetch_news_details, fetch_youtube_feed
from .reddit import scrape_reddit
from .news_analyzer import analyze_unprocessed_news

def resolve_keyword_to_item(session, keyword: str) -> Item or None:
    """
    Attempts to match a text keyword/name to an actual tradeable database item.
    """
    keyword_clean = keyword.strip()
    if not keyword_clean:
        return None
        
    # Exact case-insensitive match
    item = session.query(Item).filter(Item.name.ilike(keyword_clean), Item.tradeable == True).first()
    if item:
        return item
        
    # Partial match
    item = session.query(Item).filter(Item.name.ilike(f"%{keyword_clean}%"), Item.tradeable == True).first()
    return item

def get_latest_item_price(session, item_id: int) -> float or None:
    """
    Gets the most recent average high/low price of an item from PricePoint table.
    """
    pp = (
        session.query(PricePoint)
        .filter(PricePoint.item_id == item_id)
        .order_by(PricePoint.ts.desc())
        .first()
    )
    if not pp:
        return None
    if pp.avg_high and pp.avg_low:
        return (pp.avg_high + pp.avg_low) / 2.0
    return pp.avg_high or pp.avg_low or None

def run_speculation_cycle() -> int:
    """
    Runs the full speculation cycle:
    1. Fetch news archive, Youtube updates, and Reddit threads.
    2. Analyze updates with the LLM news_analyzer to compute NewsImpact mappings.
    3. Generate buy Recommendation opportunities for highly confident positive sentiment items.
    
    Returns: Number of new news speculation recommendations generated.
    """
    # 1. Scrape updates
    print("[Speculator] Pulling OSRS news posts...")
    posts = fetch_news_archive()
    for p in posts:
        fetch_news_details(p)
        
    print("[Speculator] Pulling YouTube videos...")
    yt_posts = fetch_youtube_feed()
    for p in yt_posts:
        fetch_news_details(p)
    
    print("[Speculator] Scraping r/2007scape popular threads...")
    scrape_reddit()
    
    print("[Speculator] Scraping Telegram & Discord channels...")
    from .social_sentiment import run_social_sentiment_scraping
    run_social_sentiment_scraping()
    
    # 2. Analyze news
    print("[Speculator] Analyzing unprocessed items with Gemini model...")
    analyze_unprocessed_news()
    
    # 3. Create Recommendations
    session = get_session()
    
    # Query impacts from the last 3 days to find speculative buy targets
    time_cutoff = datetime.utcnow() - timedelta(days=3)
    impacts = (
        session.query(NewsImpact, NewsPost)
        .join(NewsPost, NewsImpact.news_post_id == NewsPost.id)
        .filter(
            NewsPost.date >= time_cutoff,
            NewsImpact.direction == "up",
            NewsImpact.confidence >= 0.70,
            NewsImpact.item_name_keywords != "none"
        )
        .all()
    )
    
    new_recs_count = 0
    
    for imp, post in impacts:
        keywords = [k.strip() for k in imp.item_name_keywords.split(",") if k.strip()]
        for kw in keywords:
            item = resolve_keyword_to_item(session, kw)
            if not item:
                continue
                
            current_price = get_latest_item_price(session, item.id)
            if not current_price or current_price <= 0:
                continue
                
            # Check for duplicate recommendations in the last 24 hours
            dup = (
                session.query(Recommendation)
                .filter(
                    Recommendation.item_id == item.id,
                    Recommendation.signal_type == "news",
                    Recommendation.created_at >= datetime.utcnow() - timedelta(hours=24)
                )
                .first()
            )
            if dup:
                continue
                
            # Suggest quantity: default to GE limit, or default to 10 if limit is not set
            qty = item.limit if item.limit and item.limit > 0 else 10
            expected_pct = imp.expected_move_pct
            expected_profit = current_price * qty * expected_pct
            
            rec = Recommendation(
                strategy_name="news_speculation",
                item_id=item.id,
                side="buy",
                qty=qty,
                price_each=int(current_price),
                expected_profit_gp=expected_profit,
                expected_return_pct=expected_pct,
                signal_type="news",
                reason=f"Speculative buy due to sentiment ({post.category or 'news'}): '{post.title}'. Reasoning: {imp.reasoning}"
            )
            
            session.add(rec)
            new_recs_count += 1
            print(f"[Speculator] Generated buy recommendation: {qty}x {item.name} @ {current_price:,.0f} GP (Expected move: +{expected_pct*100:.1f}%)")
            
    session.commit()
    session.close()
    print(f"[Speculator] Speculation cycle complete. Generated {new_recs_count} recommendations.")
    return new_recs_count
