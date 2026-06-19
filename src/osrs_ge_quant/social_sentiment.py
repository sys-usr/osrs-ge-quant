# src/osrs_ge_quant/social_sentiment.py
import os
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any

from .db import get_session
from .models import NewsPost
from .config import load_settings

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

def scrape_telegram_channel(channel_name: str) -> List[Dict[str, Any]]:
    """
    Scrapes the public Telegram channel web preview.
    """
    url = f"https://t.me/s/{channel_name}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f"[Social Sentiment] Telegram public channel {channel_name} fetch failed: status {r.status_code}")
            return []
            
        soup = BeautifulSoup(r.text, "html.parser")
        messages = []
        
        for msg_div in soup.select(".tgme_widget_message"):
            text_el = msg_div.select_one(".tgme_widget_message_text")
            if not text_el:
                continue
                
            text = text_el.get_text("\n", strip=True)
            
            time_el = msg_div.select_one("time")
            pub_date = None
            if time_el and time_el.get("datetime"):
                try:
                    pub_date = pd.to_datetime(time_el.get("datetime")).tz_localize(None).to_pydatetime()
                except Exception:
                    pub_date = datetime.utcnow()
            else:
                pub_date = datetime.utcnow()
                
            link_el = msg_div.select_one(".tgme_widget_message_date")
            msg_url = link_el.get("href") if link_el else f"https://t.me/{channel_name}"
            
            messages.append({
                "content": text,
                "date": pub_date,
                "url": msg_url
            })
            
        return messages
    except Exception as e:
        print(f"[Social Sentiment] Telegram scrape error for {channel_name}: {e}")
        return []

def scrape_discord_channel(channel_id: str, token: str) -> List[Dict[str, Any]]:
    """
    Scrapes a Discord channel via HTTP GET requests using a bot/user token.
    """
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages?limit=20"
    headers = {"Authorization": token}
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 401 and not token.startswith("Bot "):
            # Try as a bot token
            headers = {"Authorization": f"Bot {token}"}
            resp = requests.get(url, headers=headers, timeout=10)
            
        if resp.status_code == 200:
            messages = resp.json()
            results = []
            for m in messages:
                content = m.get("content", "").strip()
                if not content:
                    continue
                
                # Parse timestamp
                ts_str = m.get("timestamp", "")
                try:
                    pub_date = pd.to_datetime(ts_str).tz_localize(None).to_pydatetime()
                except Exception:
                    pub_date = datetime.utcnow()
                    
                msg_id = m.get("id", "")
                msg_url = f"https://discord.com/channels/@me/{channel_id}/{msg_id}"
                author_name = m.get("author", {}).get("username", "Unknown")
                
                results.append({
                    "content": f"[{author_name}]: {content}",
                    "date": pub_date,
                    "url": msg_url
                })
            return results
        else:
            print(f"[Social Sentiment] Discord scrap failed for channel {channel_id} (Status {resp.status_code})")
    except Exception as e:
        print(f"[Social Sentiment] Discord scraping error: {e}")
        
    return []

def run_social_sentiment_scraping() -> List[NewsPost]:
    """
    Executes public Telegram web scraping and Discord API checks,
    persisting new sentiment messages to the news_posts table.
    """
    settings = load_settings()
    sentiment_cfg = settings.get("sentiment", {})
    
    # 1. Scrape Telegram public channels
    tg_channels = sentiment_cfg.get("telegram_channels", ["osrs_flipping", "osrs_ge_alerts"])
    new_posts = []
    session = get_session()
    
    for ch in tg_channels:
        print(f"[Social Sentiment] Scraped Telegram channel: {ch}")
        msgs = scrape_telegram_channel(ch)
        for m in msgs:
            # Skip duplicates
            existing = session.query(NewsPost).filter_by(url=m["url"]).first()
            if not existing:
                title_preview = m["content"][:100] + "..." if len(m["content"]) > 100 else m["content"]
                np = NewsPost(
                    title=f"[Telegram @{ch}] {title_preview}",
                    url=m["url"],
                    date=m["date"],
                    category="telegram",
                    summary=m["content"][:5000],
                    raw_text=m["content"][:20000]
                )
                session.add(np)
                new_posts.append(np)
                
    # 2. Scrape Discord channels if token is configured
    discord_token = os.getenv("DISCORD_USER_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    discord_channels_str = os.getenv("DISCORD_CHANNELS") or ""
    
    if discord_token and discord_channels_str:
        channel_ids = [cid.strip() for cid in discord_channels_str.split(",") if cid.strip()]
        for cid in channel_ids:
            print(f"[Social Sentiment] Scraped Discord channel ID: {cid}")
            msgs = scrape_discord_channel(cid, discord_token)
            for m in msgs:
                existing = session.query(NewsPost).filter_by(url=m["url"]).first()
                if not existing:
                    title_preview = m["content"][:100] + "..." if len(m["content"]) > 100 else m["content"]
                    np = NewsPost(
                        title=f"[Discord Channel {cid}] {title_preview}",
                        url=m["url"],
                        date=m["date"],
                        category="discord",
                        summary=m["content"][:5000],
                        raw_text=m["content"][:20000]
                    )
                    session.add(np)
                    new_posts.append(np)
                    
    session.commit()
    session.close()
    
    print(f"[Social Sentiment] Added {len(new_posts)} new social posts to database.")
    return new_posts
