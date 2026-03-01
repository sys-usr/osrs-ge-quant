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
