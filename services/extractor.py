import os
import re
import shutil
import tempfile
import uuid
import yt_dlp
import trafilatura
from PyPDF2 import PdfReader


def _fetch_html(url: str) -> str | None:
    """Fetch HTML with trafilatura, falling back to curl_cffi for sites with bot protection."""
    html = trafilatura.fetch_url(url)
    if html:
        return html
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(url, impersonate="chrome", timeout=15)
        if resp.status_code == 200 and resp.text:
            return resp.text
    except Exception as e:
        print(f"curl_cffi fallback failed: {e}")
    return None


def _extract_og_image(html: str) -> str | None:
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html, re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html, re.IGNORECASE
        )
    return m.group(1) if m else None


def extract_from_url(url: str) -> tuple[str, str | None]:
    """Extract text and og:image thumbnail from a web page."""
    html = _fetch_html(url)
    if not html:
        return "", None

    text = trafilatura.extract(html) or ""
    thumbnail = _extract_og_image(html)
    return text, thumbnail


def _ydl_base_opts(tmpdir: str) -> dict:
    opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['zh-Hans', 'zh-Hant', 'zh', 'en'],
        'subtitlesformat': 'vtt',
        'outtmpl': os.path.join(tmpdir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_embedded', 'android', 'web'],
            }
        },
    }
    # Use Chrome cookies if available — bypasses bot detection
    try:
        import browser_cookie3  # noqa: F401
        opts['cookiesfrombrowser'] = ('chrome',)
    except Exception:
        pass
    return opts


def _parse_vtt(path: str) -> str:
    """Strip VTT timing/metadata lines, return plain transcript text."""
    seen = set()
    lines = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or '-->' in line or line.startswith('WEBVTT') or line.isdigit():
                continue
            # Strip inline VTT tags like <00:00:01.000><c>text</c>
            line = re.sub(r'<[^>]+>', '', line)
            if line and line not in seen:
                seen.add(line)
                lines.append(line)
    return ' '.join(lines)


def extract_from_youtube(url: str) -> tuple[str, str | None]:
    """Extract subtitles (or description) and thumbnail from a YouTube video."""
    with tempfile.TemporaryDirectory() as tmpdir:
        opts = _ydl_base_opts(tmpdir)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

            if not info:
                return "", None

            title = info.get('title', '')
            description = info.get('description', '')
            thumbnail = info.get('thumbnail')

            sub_text = ""
            for fname in os.listdir(tmpdir):
                if fname.endswith('.vtt'):
                    try:
                        sub_text = _parse_vtt(os.path.join(tmpdir, fname))
                        if sub_text:
                            break
                    except Exception as e:
                        print(f"Warning: VTT parse error ({e})")

            text = f"{title}\n\n{sub_text}" if sub_text else f"{title}\n\n{description}"
            return text.strip(), thumbnail

        except Exception as e:
            print(f"Error extracting from YouTube: {e}")
            return "", None


def extract_from_pdf(file_path: str) -> tuple[str, None]:
    """Extract text from a PDF file."""
    text = ""
    try:
        reader = PdfReader(file_path)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    except Exception as e:
        print(f"Error reading PDF: {e}")
    return text, None


def extract_from_txt(file_path: str) -> tuple[str, None]:
    """Extract text from a TXT file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read(), None
    except Exception as e:
        print(f"Error reading TXT: {e}")
        return "", None


def download_youtube_audio(url: str, out_dir: str = "static/audio") -> str | None:
    """Download YouTube audio in best available format (no ffmpeg needed).
    Returns filename or None."""
    os.makedirs(out_dir, exist_ok=True)
    out_name = uuid.uuid4().hex

    with tempfile.TemporaryDirectory() as tmpdir:
        dest_name = f"{out_name}.mp3"
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, f"{out_name}.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "128",
            }],
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "fragment_retries": 3,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            print(f"Audio download failed: {e}")
            return None

        mp3_path = os.path.join(tmpdir, dest_name)
        if os.path.exists(mp3_path):
            shutil.copy(mp3_path, os.path.join(out_dir, dest_name))
            print(f"Audio saved: {dest_name}")
            return dest_name

    print("Audio download: no output file found")
    return None


def extract_content(source: str, source_type: str = 'url') -> tuple[str, str | None]:
    """
    Main entry point. Returns (text, thumbnail_url | None).
    source_type: 'url' | 'youtube' | 'pdf' | 'txt'
    """
    if source_type == 'youtube' or 'youtube.com' in source or 'youtu.be' in source:
        return extract_from_youtube(source)
    elif source_type == 'url' or source.startswith('http'):
        return extract_from_url(source)
    elif source_type == 'pdf':
        return extract_from_pdf(source)
    elif source_type == 'txt':
        return extract_from_txt(source)
    return "", None
