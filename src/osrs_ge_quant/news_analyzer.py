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


def analyze_unprocessed_news():
    settings = load_settings()
    if not settings.get("news", {}).get("use_chatgpt", False):
        print("[NEWS] ChatGPT analysis disabled in settings.")
        return

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[NEWS] OPENAI_API_KEY not set; skipping news analysis.")
        return

    client = OpenAI(api_key=api_key)
    session = get_session()

    # Only look at OSRS posts
    posts = (
        session.query(NewsPost)
        .filter(NewsPost.url.contains("oldschool=1"))
        .all()
    )

    for post in posts:
        existing = (
            session.query(NewsImpact)
            .filter_by(news_post_id=post.id)
            .first()
        )
        if existing:
            continue
        if not post.raw_text:
            continue

        print(f"[NEWS] Analyzing: {post.title}")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Title: {post.title}\n\nBody:\n{post.raw_text}",
            },
        ]

        try:
            resp = client.chat.completions.create(
                model=settings["news"]["openai_model"],
                response_format={"type": "json_object"},
                messages=messages,
            )
        except OpenAIError as e:
            print(f"[NEWS] OpenAI error on post {post.id}: {e}")
            break

        raw_text = resp.choices[0].message.content

        try:
            data = json.loads(raw_text)
            impacts: List[dict] = data.get("impacts", [])
        except Exception as e:
            print(f"[NEWS] Failed to parse JSON for post {post.id}: {e}")
            print(f"[NEWS] Raw response was: {raw_text[:300]}...")
            continue

        if not impacts:
            print(f"[NEWS] No item impacts inferred for post {post.id} ('{post.title}')")
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
