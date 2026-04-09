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

def _build_feed(base_url: str, episodes: list) -> object:
    fg = FeedGenerator()
    fg.load_extension('podcast')

    fg.title('我的专属播客')
    fg.description('由 podcast_generator 自动生成的播客节目。')
    fg.link(href=base_url, rel='alternate')
    fg.language('zh-CN')
    fg.podcast.itunes_author('podcast_generator')
    fg.podcast.itunes_explicit('no')
    fg.podcast.itunes_category('Technology')
    fg.podcast.itunes_image('https://avatars.githubusercontent.com/TU-Dan')

    for ep in episodes:
        fe = fg.add_entry()
        fe.id(ep['audio_url'])
        fe.title(ep['title'])
        fe.description(ep['description'])
        fe.enclosure(ep['audio_url'], str(ep['audio_length']), 'audio/mpeg')
        fe.published(ep['published'])
        fe.podcast.itunes_explicit('no')
        if ep.get('episode_image'):
            fe.podcast.itunes_image(ep['episode_image'])

    return fg


def generate_rss(base_url: str = "http://localhost:8000"):
    fg = _build_feed(base_url, load_episodes())
    fg.rss_file("static/podcast.xml")


def generate_rss_for_export(pages_base_url: str, output_path: str):
    """Regenerate RSS with GitHub Pages URLs for public hosting."""
    pages_base_url = pages_base_url.rstrip("/")
    episodes = load_episodes()

    export_episodes = []
    for ep in episodes:
        audio_filename = ep['audio_url'].split('/')[-1]
        updated = {**ep, "audio_url": f"{pages_base_url}/audio/{audio_filename}"}

        # Rewrite episode image URL if it's a local static path
        if ep.get('episode_image'):
            img_filename = ep['episode_image'].split('/')[-1]
            updated['episode_image'] = f"{pages_base_url}/images/{img_filename}"

        export_episodes.append(updated)

    fg = _build_feed(pages_base_url, export_episodes)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fg.rss_file(output_path)


def add_episode(title: str, description: str, audio_filename: str, audio_length: int,
                base_url: str = "http://localhost:8000", episode_image: str = None):
    episodes = load_episodes()
    audio_url = f"{base_url}/static/audio/{audio_filename}"

    ep = {
        "title": title,
        "description": description,
        "audio_url": audio_url,
        "audio_length": audio_length,
        "published": datetime.now(timezone.utc).isoformat(),
    }
    if episode_image:
        ep["episode_image"] = episode_image

    episodes.insert(0, ep)
    save_episodes(episodes)
    generate_rss(base_url)
