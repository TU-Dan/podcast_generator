import xml.etree.ElementTree as ET
from curl_cffi import requests
from services.rss import load_episodes, save_episodes, generate_rss


def import_from_rss(rss_url: str, local_base_url: str) -> tuple[int, int]:
    """
    Import episodes from a remote RSS feed into local episodes.json.
    Returns (imported_count, skipped_count).
    """
    resp = requests.get(rss_url, timeout=15, impersonate="chrome")
    resp.raise_for_status()
    xml_data = resp.content

    root = ET.fromstring(xml_data)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("Invalid RSS: no <channel> found")

    existing = load_episodes()
    existing_urls = {ep["audio_url"] for ep in existing}

    imported = 0
    skipped = 0
    new_episodes = []

    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue

        audio_url = enclosure.get("url", "")
        if not audio_url:
            continue

        if audio_url in existing_urls:
            skipped += 1
            continue

        title = item.findtext("title", default="Untitled")
        description = item.findtext("description", default="")
        pub_date = item.findtext("pubDate", default="")
        length = int(enclosure.get("length", 0))

        # Convert to ISO format for consistency
        from email.utils import parsedate_to_datetime
        try:
            published = parsedate_to_datetime(pub_date).isoformat()
        except Exception:
            from datetime import datetime, timezone
            published = datetime.now(timezone.utc).isoformat()

        new_episodes.append({
            "title": title,
            "description": description or title,
            "audio_url": audio_url,
            "audio_length": length,
            "published": published,
        })
        existing_urls.add(audio_url)
        imported += 1

    if new_episodes:
        # Merge and re-sort by publish date (newest first)
        merged = new_episodes + existing
        merged.sort(key=lambda x: x["published"], reverse=True)
        save_episodes(merged)
        generate_rss(local_base_url)

    return imported, skipped
