import os
import json
from datetime import datetime, timezone
from feedgen.feed import FeedGenerator

DB_FILE = "static/episodes.json"

def load_episodes():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_episodes(episodes):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)

def generate_rss(base_url: str = "http://localhost:8000"):
    episodes = load_episodes()
    
    fg = FeedGenerator()
    fg.load_extension('podcast')
    
    fg.title('My Personal Podcast')
    fg.description('Generated audio from web articles, PDFs, and YouTube videos.')
    fg.link(href=base_url, rel='alternate')
    fg.language('zh-CN')
    
    for ep in episodes:
        fe = fg.add_entry()
        fe.id(ep['audio_url'])
        fe.title(ep['title'])
        fe.description(ep['description'])
        fe.enclosure(ep['audio_url'], str(ep['audio_length']), 'audio/mpeg')
        fe.published(ep['published'])
        
    fg.rss_file("static/podcast.xml")

def generate_rss_for_export(pages_base_url: str, output_path: str):
    """Regenerate RSS with GitHub Pages URLs for public hosting."""
    pages_base_url = pages_base_url.rstrip("/")
    episodes = load_episodes()

    fg = FeedGenerator()
    fg.load_extension('podcast')
    fg.title('My Personal Podcast')
    fg.description('Generated audio from web articles, PDFs, and YouTube videos.')
    fg.link(href=pages_base_url, rel='alternate')
    fg.language('zh-CN')

    for ep in episodes:
        filename = ep['audio_url'].split('/')[-1]
        audio_url = f"{pages_base_url}/audio/{filename}"

        fe = fg.add_entry()
        fe.id(audio_url)
        fe.title(ep['title'])
        fe.description(ep['description'])
        fe.enclosure(audio_url, str(ep['audio_length']), 'audio/mpeg')
        fe.published(ep['published'])

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fg.rss_file(output_path)


def add_episode(title: str, description: str, audio_filename: str, audio_length: int, base_url: str = "http://localhost:8000"):
    episodes = load_episodes()
    
    audio_url = f"{base_url}/static/audio/{audio_filename}"
    
    episodes.insert(0, {
        "title": title,
        "description": description,
        "audio_url": audio_url,
        "audio_length": audio_length,
        "published": datetime.now(timezone.utc).isoformat()
    })
    
    save_episodes(episodes)
    generate_rss(base_url)
