# src/osrs_ge_quant/news.py
import datetime as dt
from typing import List, Union

import requests
from bs4 import BeautifulSoup

from .db import get_session
from .models import NewsPost

BASE_URL = "https://secure.runescape.com"
# OSRS-specific archive, not RS3:
NEWS_ARCHIVE_URL = (
    "https://secure.runescape.com/m=news/archive?oldschool=1"
)

HEADERS = {
    "User-Agent": "osrs-ge-quant/0.1 (+https://github.com/yourname/osrs-ge-quant)"
}


def _absolute_url(href: str) -> str:
    """Turn relative hrefs into absolute URLs."""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    # Relative path like "sailing---resources--skilling-activities-poll?oldschool=1"
    return f"{BASE_URL}/m=news/{href}" if "m=news" not in href else BASE_URL + "/" + href


def fetch_news_archive() -> List[NewsPost]:
    """
    Fetch the OSRS news archive landing page and insert new posts into the DB.

    Uses the existing NewsPost fields:
      - title
      - url
      - date (we'll set this to now, since archive page doesn't expose it cleanly)
      - raw_text (left None until details are fetched)
    """
    session = get_session()

    resp = requests.get(NEWS_ARCHIVE_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    new_posts: List[NewsPost] = []

    for a in soup.find_all("a"):
        href = a.get("href") or ""
        title = a.get_text(strip=True)

        if not href or not title:
            continue

        # Only OSRS news posts: must be m=news with oldschool=1
        if "m=news" not in href:
            continue
        if "oldschool=1" not in href:
            continue
        # Skip archive/self links
        if "archive" in href:
            continue

        url = _absolute_url(href)

        existing = (
            session.query(NewsPost)
            .filter(NewsPost.url == url)
            .first()
        )
        if existing:
            continue

        post = NewsPost(
            title=title,
            url=url,
            date=dt.datetime.utcnow(),
            raw_text=None,
        )
        session.add(post)
        new_posts.append(post)

    session.commit()
    print(f"[NEWS] Added {len(new_posts)} new archive entries.")
    return new_posts


def fetch_news_details(post: Union[NewsPost, int]) -> NewsPost | None:
    """
    Given a NewsPost (or its ID), fetch and store its extracted text in raw_text.
    """
    session = get_session()

    # Re-attach or load inside this session
    if isinstance(post, int):
        post_obj = session.get(NewsPost, post)
        if post_obj is None:
            print(f"[NEWS] NewsPost id={post} not found.")
            return None
    else:
        post_obj = session.merge(post)

    # If we've already got text, skip
    if post_obj.raw_text:
        return post_obj

    try:
        resp = requests.get(post_obj.url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[NEWS] Failed to fetch {post_obj.url}: {e}")
        return post_obj

    soup = BeautifulSoup(resp.text, "html.parser")

    # Try a few likely containers, fall back to whole body
    body = (
        soup.select_one(".news-article")
        or soup.select_one(".news-article__body")
        or soup.select_one("#news-article")
        or soup.body
    )

    text = body.get_text("\n", strip=True) if body else ""
    post_obj.raw_text = text[:20000]

    session.add(post_obj)
    session.commit()

    print(f"[NEWS] Fetched body for '{post_obj.title}'")
    return post_obj


def fetch_youtube_feed() -> List[NewsPost]:
    """
    Fetch the latest video uploads from FlippingOldschool's YouTube RSS feed,
    and insert new entries as NewsPost rows (category='youtube').
    """
    import xml.etree.ElementTree as ET
    import pandas as pd
    from .db import get_session
    from .models import NewsPost

    channel_id = "UCIi4nY4YuOYUJEg8XLM0vQw"
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[YouTube] Failed to fetch feed: {e}")
        return []

    try:
        root = ET.fromstring(r.content)
        # XML Namespaces
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/"
        }
        
        session = get_session()
        new_posts = []

        # Find all <entry> tags
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link", ns)
            pub_el = entry.find("atom:published", ns)

            if title_el is None or link_el is None or pub_el is None:
                continue

            title = title_el.text.strip()
            video_url = link_el.attrib.get("href")
            published_str = pub_el.text

            # Parse timezone naive datetime
            published_dt = pd.to_datetime(published_str).tz_localize(None).to_pydatetime()

            # Get description / media details if available
            media_group = entry.find("media:group", ns)
            description = ""
            if media_group is not None:
                media_desc = media_group.find("media:description", ns)
                if media_desc is not None and media_desc.text:
                    description = media_desc.text.strip()

            # Skip if duplicate
            existing = session.query(NewsPost).filter_by(url=video_url).first()
            if not existing:
                np = NewsPost(
                    title=title,
                    url=video_url,
                    date=published_dt,
                    category="youtube",
                    summary="YouTube Upload from FlippingOldSchool",
                    raw_text=description[:5000] # store first 5k chars of description
                )
                session.add(np)
                new_posts.append(np)

        session.commit()
        session.close()
        print(f"[YouTube] Added {len(new_posts)} new video uploads to database.")
        return new_posts
    except Exception as e:
        print(f"[YouTube] Error parsing feed: {e}")
        return []
