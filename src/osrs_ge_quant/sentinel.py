# src/osrs_ge_quant/sentinel.py
import time
from datetime import datetime
import traceback

from .news import fetch_news_archive, fetch_news_details, fetch_youtube_feed
from .reddit import scrape_reddit
from .social_sentiment import run_social_sentiment_scraping
from .news_analyzer import analyze_unprocessed_news
from .engine import run_full_cycle
from .config import load_settings

def run_sentinel_daemon():
    print("[Sentinel] Continuous Day-Trading Sentinel Daemon started.")
    last_digest_time = None
    
    try:
        while True:
            settings = load_settings()
            daemon_settings = settings.get("daemon", {})
            interval_mins = daemon_settings.get("interval_minutes", 5)
            
            start_time = time.time()
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"\n[Sentinel] [{now_str}] Starting cycle update...")
            
            try:
                # 1. Fetch and analyze OSRS news & Reddit posts
                print("[Sentinel] Fetching OSRS news updates...")
                posts = fetch_news_archive()
                print(f"[Sentinel] Fetched {len(posts)} new archive entries.")
                for p in posts:
                    fetch_news_details(p)
                
                print("[Sentinel] Fetching YouTube updates...")
                yt_posts = fetch_youtube_feed()
                print(f"[Sentinel] Fetched {len(yt_posts)} new YouTube video uploads.")
                for p in yt_posts:
                    fetch_news_details(p)
                
                print("[Sentinel] Scraping r/2007scape Reddit discussions...")
                scrape_reddit()
                
                print("[Sentinel] Scraping Telegram & Discord channels...")
                run_social_sentiment_scraping()
                
                print("[Sentinel] Running sentiment analysis on news & Reddit posts...")
                analyze_unprocessed_news()

                # 2. Run price cycle & hot flip checks
                # Send digest on first run or every 12 hours
                should_send_digest = (last_digest_time is None or (time.time() - last_digest_time) >= 12 * 3600)
                print(f"[Sentinel] Running full analysis cycle (send_digest={should_send_digest})...")
                run_full_cycle(send_digest=should_send_digest)
                if should_send_digest:
                    last_digest_time = time.time()
                    
                print(f"[Sentinel] [{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}] Cycle completed successfully.")
            except Exception as e:
                print(f"[Sentinel] [Error] Exception in cycle execution: {e}")
                traceback.print_exc()
            
            elapsed = time.time() - start_time
            sleep_time = max(0.0, (interval_mins * 60.0) - elapsed)
            print(f"[Sentinel] Sleeping for {sleep_time / 60.0:.2f} minutes until next update.")
            time.sleep(sleep_time)
            
    except KeyboardInterrupt:
        print("\n[Sentinel] KeyboardInterrupt received. Shutting down sentinel gracefully.")

if __name__ == "__main__":
    run_sentinel_daemon()
