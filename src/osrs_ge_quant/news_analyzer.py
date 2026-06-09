# src/osrs_ge_quant/news_analyzer.py
import json
import os
from typing import List

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError

from .db import get_session
from .models import NewsPost, NewsImpact
from .config import load_settings

# Ensure .env is loaded for CLI processes
load_dotenv()

SYSTEM_PROMPT = """
You are an expert Old School RuneScape (OSRS) market analyst.

You are given an OSRS news post (update, blog, poll, announcement etc.).
Your job is to decide whether this post is likely to move prices of specific
tradeable OSRS items on the Grand Exchange, and if so, how.

Very important:
- This is **Old School RuneScape**, NOT RuneScape 3.
- Use OSRS item names and OSRS terminology only.
- If the post is clearly not about gameplay, items, bosses, skilling, rewards,
  or mechanics (e.g. "Website", "Support", "Survey", "Your Feedback"),
  then output an empty `impacts` list.

Output format:
Return a single JSON object with this shape:

{
  "impacts": [
    {
      "item_keywords": ["Bandos godsword", "Saradomin godsword"],
      "direction": "up",          // "up" or "down"
      "confidence": 0.7,          // 0.0 - 1.0
      "expected_move_pct": 0.15,  // 0.10 means +10%
      "reasoning": "Short explanation..."
    },
    ...
  ]
}

Guidelines:
- Only include items that have a reasonably direct connection to the content
  of the post (new boss weak to certain styles, new skilling method, new BIS item, etc.).
- It's okay if `impacts` is an empty list when the post is meta / website / survey / etc.
- When unsure, be conservative and either:
  - omit the item, or
  - include it with low confidence (e.g. 0.2) and small expected_move_pct.
"""

REDDIT_SYSTEM_PROMPT = """
You are an OSRS (Old School RuneScape) market analyst and sentiment tracker.

You are given a popular post from the r/2007scape community forum.
Your job is to decide whether this post represents a significant market sentiment shift (e.g. hype, panic selling, item nerf fear, buff speculation) that is likely to move the Grand Exchange price of specific tradeable OSRS items.

Only include items that are explicitly mentioned or strongly implied by the title and content.
If the thread is just general gameplay discussion, achievements, questions, memes, humor, or has no clear economic effect on specific items, return an empty `impacts` list.

Output format:
Return a single JSON object with this shape:

{
  "impacts": [
    {
      "item_keywords": ["item name"],
      "direction": "up",          // "up" (hype, buying pressure) or "down" (panic, nerf fears)
      "confidence": 0.8,          // 0.0 - 1.0
      "expected_move_pct": 0.15,  // 0.10 means +10%
      "reasoning": "Explain the OSRS reddit community sentiment context..."
    }
  ]
}
"""


YOUTUBE_SYSTEM_PROMPT = """
You are an OSRS (Old School RuneScape) market analyst and trading sentiment tracker.

You are given a YouTube video title and description from a famous OSRS day trader.
Your job is to decide whether this video represents a speculative market shock (e.g., highlighting a specific item to flip, announcing panic buys, or predicting price movements) that is likely to move the Grand Exchange price of specific tradeable OSRS items.

Flippers and copycat traders will immediately react to this video, so focus on direct recommendations or strong hype.
If the video is a general progression series (e.g. "Road to Max", "Loot from 1000 Corporeal Beast"), general skilling, or has no short-term economic speculation on specific items, return an empty `impacts` list.

Output format:
Return a single JSON object with this shape:

{
  "impacts": [
    {
      "item_keywords": ["item name"],
      "direction": "up",          // "up" (hype, buying pressure) or "down" (panic, selloff)
      "confidence": 0.8,          // 0.0 - 1.0
      "expected_move_pct": 0.10,  // 0.10 means +10%
      "reasoning": "Explain why this video will cause short-term copycat speculation..."
    }
  ]
}
"""


def analyze_unprocessed_news():
    settings = load_settings()
    news_settings = settings.get("news", {})
    if not news_settings.get("use_chatgpt", False):
        print("[NEWS] Gemini news analysis disabled in settings.")
        return

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[NEWS] GEMINI_API_KEY or OPENAI_API_KEY not set; skipping news analysis.")
        return

    # Connect to Gemini's OpenAI compatibility endpoint if using GEMINI_API_KEY
    is_gemini = os.getenv("GEMINI_API_KEY") is not None or os.getenv("OPENAI_API_KEY") is None
    base_url = "https://generativelanguage.googleapis.com/v1beta/openai/" if is_gemini else None

    client = OpenAI(api_key=api_key, base_url=base_url)
    session = get_session()

    # Retrieve all posts (official news, scraped Reddit posts, and YouTube uploads)
    posts = session.query(NewsPost).all()

    for post in posts:
        is_reddit = post.category == "reddit"
        is_youtube = post.category == "youtube"
        is_official = "oldschool=1" in post.url
        if not is_reddit and not is_official and not is_youtube:
            continue

        existing = (
            session.query(NewsImpact)
            .filter_by(news_post_id=post.id)
            .first()
        )
        if existing:
            continue

        safe_title = post.title.encode('ascii', errors='replace').decode('ascii')
        source_label = "Reddit" if is_reddit else ("YouTube" if is_youtube else "Official")
        print(f"[NEWS] Analyzing with Gemini ({source_label}): {safe_title}")

        if is_reddit:
            prompt = REDDIT_SYSTEM_PROMPT
        elif is_youtube:
            prompt = YOUTUBE_SYSTEM_PROMPT
        else:
            prompt = SYSTEM_PROMPT
        content_body = f"Title: {post.title}\n\nContext/Body:\n{post.raw_text or post.summary or ''}"

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": content_body,
            },
        ]

        if is_gemini:
            model_name = news_settings.get("gemini_model") or "gemini-2.5-flash"
        else:
            model_name = news_settings.get("openai_model") or "gpt-4o-mini"

        import time
        max_retries = 3
        retry_delay = 15
        resp = None
        for attempt in range(max_retries):
            try:
                resp = client.chat.completions.create(
                    model=model_name,
                    response_format={"type": "json_object"},
                    messages=messages,
                )
                break
            except OpenAIError as e:
                is_rate_limit = "429" in str(e) or "quota" in str(e).lower() or "rate" in str(e).lower()
                if is_rate_limit and attempt < max_retries - 1:
                    print(f"[NEWS] Rate limit hit on post {post.id}. Sleeping {retry_delay}s before retry (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(retry_delay)
                    continue
                else:
                    print(f"[NEWS] API error on post {post.id}: {e}")
                    break

        if resp is None:
            continue

        raw_text = resp.choices[0].message.content

        try:
            data = json.loads(raw_text)
            impacts: List[dict] = data.get("impacts", [])
        except Exception as e:
            print(f"[NEWS] Failed to parse JSON for post {post.id}: {e}")
            print(f"[NEWS] Raw response was: {raw_text[:300]}...")
            continue

        if not impacts:
            safe_title = post.title.encode('ascii', errors='replace').decode('ascii')
            print(f"[NEWS] No item impacts inferred for post {post.id} ('{safe_title}')")
            # Store a dummy impact to avoid re-analyzing this post and wasting API calls
            ni = NewsImpact(
                news_post_id=post.id,
                item_name_keywords="none",
                direction="none",
                confidence=0.0,
                expected_move_pct=0.0,
                reasoning="No market impact identified by model."
            )
            session.add(ni)
            session.commit()
            continue

        count = 0
        for imp in impacts:
            item_keywords = imp.get("item_keywords") or imp.get("items") or []
            if not item_keywords:
                continue

            try:
                confidence = float(imp.get("confidence", 0.5))
            except Exception:
                confidence = 0.5

            try:
                expected_move_pct = float(imp.get("expected_move_pct", 0.05))
            except Exception:
                expected_move_pct = 0.05

            ni = NewsImpact(
                news_post_id=post.id,
                item_name_keywords=",".join(item_keywords),
                direction=imp.get("direction", "up"),
                confidence=confidence,
                expected_move_pct=expected_move_pct,
                reasoning=imp.get("reasoning", ""),
            )
            session.add(ni)
            count += 1

        session.commit()
        print(f"[NEWS] Stored {count} impacts for post {post.id}")

        # Check for urgent notification triggers on new impacts
        from .portfolio import load_open_positions
        from .notifications import send_urgent_news_alert

        positions_df = load_open_positions()
        portfolio_items = []
        if not positions_df.empty:
            portfolio_items = [name.lower() for name in positions_df["item_name"].tolist() if name]

        for imp in impacts:
            item_keywords = imp.get("item_keywords") or imp.get("items") or []
            if not item_keywords:
                continue

            direction = imp.get("direction", "up")
            try:
                confidence = float(imp.get("confidence", 0.5))
                expected_move_pct = float(imp.get("expected_move_pct", 0.05))
            except Exception:
                confidence = 0.5
                expected_move_pct = 0.05
            reasoning = imp.get("reasoning", "")

            # Match keywords to user's portfolio items (case-insensitive submatch)
            in_portfolio = False
            for keyword in item_keywords:
                kw_lower = keyword.lower()
                if any(kw_lower in p_item or p_item in kw_lower for p_item in portfolio_items):
                    in_portfolio = True
                    break

            is_high_value = any(hv in kw.lower() for kw in item_keywords for hv in ["twisted bow", "tbow", "shadow", "scythe", "3rd age", "elysian"])

            # Trigger criteria:
            # - For Reddit: trigger only if held in active portfolio OR (confidence >= 0.80 and move >= 15%)
            # - For YouTube: trigger if held in active portfolio OR (confidence >= 0.80 and move >= 10%)
            # - For Official: trigger if held in portfolio OR high value (confidence >= 0.60 and move >= 5%) OR high confidence (confidence >= 0.80 and move >= 15%)
            if is_reddit:
                trigger_alert = (
                    in_portfolio or
                    (confidence >= 0.80 and expected_move_pct >= 0.15)
                )
            elif is_youtube:
                trigger_alert = (
                    in_portfolio or
                    (confidence >= 0.80 and expected_move_pct >= 0.10)
                )
            else:
                trigger_alert = (
                    in_portfolio or
                    (is_high_value and confidence >= 0.6 and expected_move_pct >= 0.05) or
                    (confidence >= 0.8 and expected_move_pct >= 0.15)
                )

            if trigger_alert:
                source_label = "Reddit" if is_reddit else ("YouTube" if is_youtube else "News")
                send_urgent_news_alert(
                    news_title=f"[{source_label}] {post.title}",
                    item_keywords=", ".join(item_keywords),
                    direction=direction,
                    expected_move=expected_move_pct,
                    confidence=confidence,
                    reasoning=reasoning,
                    in_portfolio=in_portfolio
                )
                break

