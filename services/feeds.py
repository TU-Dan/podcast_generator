import re
import json
import feedparser
import httpx
from datetime import datetime, timezone

from services.db import (
    list_feeds, update_feed_fetched, upsert_feed_items, list_all_tags,
)
from services.llm import _get_client as get_client


async def resolve_feed_url(url: str) -> tuple[str, str]:
    """Return (rss_url, feed_type). Handles YouTube channel URLs."""
    url = url.strip()
    if "youtube.com" in url or "youtu.be" in url:
        rss = await _youtube_to_rss(url)
        return rss or url, "youtube"
    return url, "rss"


async def _youtube_to_rss(url: str) -> str | None:
    # Direct channel ID
    m = re.search(r'/channel/(UC[a-zA-Z0-9_-]{22})', url)
    if m:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"
    # @handle — fetch page to extract channelId
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, follow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0"})
            m2 = re.search(r'"channelId":"(UC[a-zA-Z0-9_-]{22})"', resp.text)
            if m2:
                return f"https://www.youtube.com/feeds/videos.xml?channel_id={m2.group(1)}"
    except Exception:
        pass
    return None


def _parse_feed(url: str) -> list[dict]:
    d = feedparser.parse(url)
    items = []
    for entry in d.entries[:30]:
        desc = entry.get("summary", entry.get("description", ""))
        # Strip HTML tags from description
        desc = re.sub(r"<[^>]+>", " ", desc).strip()[:400]
        items.append({
            "title": entry.get("title", "").strip(),
            "url": entry.get("link", ""),
            "description": desc,
            "published_at": entry.get("published", entry.get("updated", "")),
        })
    return [i for i in items if i["url"]]


async def _score_batch(items: list[dict], user_tags: list[str]) -> list[int]:
    if not items:
        return []
    if not user_tags:
        return [50] * len(items)

    interest_str = "、".join(user_tags[:20])
    items_str = "\n".join(
        f"{i+1}. {it['title']}: {it['description'][:100]}"
        for i, it in enumerate(items)
    )
    prompt = (
        f"用户兴趣标签：{interest_str}\n\n"
        f"对以下文章打相关性分（0-100整数），只返回JSON整数数组，例如[85,30,72]，不要其他文字：\n\n"
        f"{items_str}"
    )
    try:
        client = get_client()
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        text = resp.choices[0].message.content.strip()
        m = re.search(r'\[[\d,\s]+\]', text)
        if m:
            arr = json.loads(m.group())
            while len(arr) < len(items):
                arr.append(50)
            return [max(0, min(100, int(s))) for s in arr[:len(items)]]
    except Exception as e:
        print(f"[feeds] score_batch error: {e}")
    return [50] * len(items)


async def refresh_feed(feed: dict) -> int:
    """Fetch one feed, score items, upsert. Returns count of new items."""
    items = _parse_feed(feed["url"])
    if not items:
        return 0

    user_tags = [t["tag"] for t in list_all_tags()[:20]]

    # Score in batches of 10
    scores = []
    for i in range(0, len(items), 10):
        batch = items[i:i+10]
        batch_scores = await _score_batch(batch, user_tags)
        scores.extend(batch_scores)

    to_insert = []
    for item, score in zip(items, scores):
        to_insert.append({
            "feed_id": feed["id"],
            "feed_name": feed["name"],
            "title": item["title"],
            "url": item["url"],
            "description": item["description"],
            "published_at": item["published_at"],
            "relevance_score": score,
        })

    upsert_feed_items(to_insert)
    update_feed_fetched(feed["id"])
    return len(to_insert)


async def refresh_all_feeds() -> int:
    """Refresh every subscribed feed. Returns total new items count."""
    feeds = list_feeds()
    total = 0
    for feed in feeds:
        try:
            n = await refresh_feed(feed)
            total += n
            print(f"[feeds] {feed['name']}: +{n} items")
        except Exception as e:
            print(f"[feeds] {feed['name']} error: {e}")
    return total


def _should_refresh(feed: dict, interval_hours: int = 4) -> bool:
    if not feed.get("last_fetched_at"):
        return True
    try:
        last = datetime.fromisoformat(feed["last_fetched_at"])
        delta = datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)
        return delta.total_seconds() > interval_hours * 3600
    except Exception:
        return True


async def refresh_stale_feeds(interval_hours: int = 4) -> int:
    feeds = [f for f in list_feeds() if _should_refresh(f, interval_hours)]
    total = 0
    for feed in feeds:
        try:
            n = await refresh_feed(feed)
            total += n
        except Exception as e:
            print(f"[feeds] {feed['name']} error: {e}")
    return total
