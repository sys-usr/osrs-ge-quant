# src/osrs_ge_quant/reddit.py
import re
from datetime import datetime
import requests
import urllib3
from bs4 import BeautifulSoup

from .db import get_session
from .models import NewsPost

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

REDLIB_MIRRORS = [
    "https://redlib.perennialte.ch",
    "https://redlib.r4fo.com",
    "https://redlib.cow.rip"
]

def scrape_reddit() -> int:
    """
    Fetch the latest hot posts from r/2007scape via Redlib public mirrors,
    filter for active discussion threads, and upsert them in the database.
    Returns: Number of new posts added.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    html_text = None
    used_mirror = None
    for mirror in REDLIB_MIRRORS:
        url = f"{mirror}/r/2007scape"
        try:
            r = requests.get(url, headers=headers, verify=False, timeout=12)
            if r.status_code == 200 and "Error" not in r.text[:200] and "Blocked" not in r.text[:200]:
                html_text = r.text
                used_mirror = mirror
                break
            else:
                print(f"[Reddit] Mirror {mirror} returned status {r.status_code}. Trying next mirror...")
        except Exception as e:
            print(f"[Reddit] Failed to connect to mirror {mirror}: {e}. Trying next mirror...")

    if not html_text:
        print("[Reddit] All Redlib mirrors failed or blocked the scraper. Skipping Reddit refresh.")
        return 0

    print(f"[Reddit] Successfully retrieved feed from {used_mirror}")
    soup = BeautifulSoup(html_text, "html.parser")
    post_divs = soup.find_all("div", class_=lambda x: x and "post" in x and "post_comments" not in x)
    
    session = get_session()
    new_count = 0

    for div in post_divs:
        post_id = div.get("id")
        if not post_id:
            continue

        # 1. Parse Title & Link
        title_h2 = div.find("h2", class_="post_title")
        if not title_h2:
            continue
        
        anchors = title_h2.find_all("a")
        title_a = None
        for a in anchors:
            href = a.get("href", "")
            if "search" in href or "flair_name" in href:
                continue
            title_a = a
            break
        
        if not title_a:
            continue

        title = title_a.text.strip()
        href = title_a.get("href", "")
        post_url = f"https://www.reddit.com{href if href.startswith('/') else '/' + href}"

        # Skip stickied automated threads like daily questions to focus on organic trading discussion
        if "have a question about the game or the subreddit" in title.lower():
            continue

        # 2. Parse score / upvotes
        score_div = div.find("div", class_="post_score")
        score = 0
        if score_div:
            score_text = score_div.text.replace("Upvotes", "").replace("Upvote", "").strip()
            try:
                score = int(score_text)
            except ValueError:
                score = 0

        # 3. Parse comment count
        comment_count = 0
        comments_a = div.find("a", href=lambda h: h and "comments" in h and h != href)
        if not comments_a:
            comments_a = div.find("a", string=lambda s: s and "comment" in s.lower())
        
        if comments_a:
            comment_text = comments_a.text.lower()
            match = re.search(r'(\d+(?:\.\d+)?)([km]?)', comment_text)
            if match:
                num = float(match.group(1))
                suffix = match.group(2)
                if suffix == 'k':
                    comment_count = int(num * 1000)
                elif suffix == 'm':
                    comment_count = int(num * 1000000)
                else:
                    comment_count = int(num)

        # Apply discussion activity triggers (must be reasonably active to cause market ripples)
        if score < 50 and comment_count < 30:
            continue

        # 4. Parse created timestamp
        created_span = div.find("span", class_="created")
        date_val = datetime.utcnow()
        if created_span and created_span.get("title"):
            title_str = created_span.get("title")
            try:
                date_val = datetime.strptime(title_str, "%b %d %Y, %H:%M:%S UTC")
            except Exception:
                pass

        # 5. Extract body preview if present
        body_div = div.find("div", class_="post_body")
        raw_text = body_div.text.strip() if body_div else ""

        # Check for duplicates in DB
        existing = session.query(NewsPost).filter_by(url=post_url).first()
        if not existing:
            np = NewsPost(
                title=title,
                url=post_url,
                date=date_val,
                category="reddit",
                summary=f"Upvotes: {score} | Comments: {comment_count}",
                raw_text=raw_text
            )
            session.add(np)
            new_count += 1

    session.commit()
    session.close()

    print(f"[Reddit] Added {new_count} high-activity OSRS Reddit threads to database.")
    return new_count
