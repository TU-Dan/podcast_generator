import os
import re
import json
from datetime import datetime, timezone
from feedgen.feed import FeedGenerator

DB_FILE = "static/episodes.json"

CHANNEL_IMAGE = "https://github.com/TU-Dan.png"
CHANNEL_TITLE = "我的专属播客"
CHANNEL_AUTHOR = "Dan Tu"
CHANNEL_EMAIL = "blanchetu@icloud.com"


def load_episodes():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_episodes(episodes):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(episodes, f, ensure_ascii=False, indent=2)


def clean_description(text: str) -> str:
    """Strip VTT/SRT markup and normalize whitespace for use as episode description."""
    # Remove VTT timestamp tags like <00:00:01.320> and <c>...</c>
    text = re.sub(r'<\d+:\d+:\d+\.\d+>', '', text)
    text = re.sub(r'</?c>', '', text)
    # Remove "Kind: captions Language: en" header lines
    text = re.sub(r'Kind:\s*captions\s*Language:\s*\S+', '', text, flags=re.IGNORECASE)
    # Collapse excessive whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_feed(base_url: str, episodes: list, self_url: str = None) -> object:
    fg = FeedGenerator()
    fg.load_extension('podcast')

    fg.title(CHANNEL_TITLE)
    fg.description('由 AI 自动生成的个人播客，内容来自网络文章和视频。')
    if self_url:
        fg.link(href=self_url, rel='self', type='application/rss+xml')
    fg.link(href=base_url, rel='alternate')
    fg.language('zh-CN')
    fg.podcast.itunes_author(CHANNEL_AUTHOR)
    fg.podcast.itunes_explicit('no')
    fg.podcast.itunes_category('Technology')
    fg.podcast.itunes_image(CHANNEL_IMAGE)
    fg.podcast.itunes_owner(name=CHANNEL_AUTHOR, email=CHANNEL_EMAIL)

    for ep in episodes:
        fe = fg.add_entry()
        fe.id(ep['audio_url'])
        fe.title(ep['title'])
        desc = clean_description(ep.get('description', ''))
        fe.description(desc)
        fe.podcast.itunes_summary(desc)
        mime = ep.get('audio_mime', 'audio/mpeg')
        fe.enclosure(ep['audio_url'], str(ep['audio_length']), mime)
        fe.published(ep['published'])
        fe.podcast.itunes_explicit('no')
        if ep.get('episode_image'):
            fe.podcast.itunes_image(ep['episode_image'])
        if ep.get('duration'):
            fe.podcast.itunes_duration(ep['duration'])

    return fg


def generate_rss(base_url: str = "http://localhost:8000"):
    self_url = f"{base_url}/podcast.xml"
    fg = _build_feed(base_url, load_episodes(), self_url=self_url)
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

    self_url = f"{pages_base_url}/podcast.xml"
    fg = _build_feed(pages_base_url, export_episodes, self_url=self_url)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fg.rss_file(output_path)


def add_episode(title: str, description: str, audio_filename: str, audio_length: int,
                base_url: str = "http://localhost:8000", episode_image: str = None,
                audio_mime: str = None):
    episodes = load_episodes()
    audio_url = f"{base_url}/static/audio/{audio_filename}"

    ep = {
        "title": title,
        "description": description,
        "audio_url": audio_url,
        "audio_length": audio_length,
        "published": datetime.now(timezone.utc).isoformat(),
        "audio_mime": audio_mime or "audio/mpeg",
    }
    if episode_image:
        ep["episode_image"] = episode_image

    episodes.insert(0, ep)
    save_episodes(episodes)
    generate_rss(base_url)
