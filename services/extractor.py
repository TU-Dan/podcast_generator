import os
import re
import tempfile
import yt_dlp
import trafilatura
from PyPDF2 import PdfReader


def extract_from_url(url: str) -> tuple[str, str | None]:
    """Extract text and og:image thumbnail from a web page."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return "", None

    text = trafilatura.extract(downloaded) or ""

    # Extract og:image
    thumbnail = None
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        downloaded, re.IGNORECASE
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            downloaded, re.IGNORECASE
        )
    if m:
        thumbnail = m.group(1)

    return text, thumbnail


def extract_from_youtube(url: str) -> tuple[str, str | None]:
    """Extract subtitles and thumbnail from a YouTube video."""
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'zh-Hans', 'zh-Hant', 'zh'],
        'outtmpl': '%(id)s.%(ext)s',
        'quiet': True,
        'ignoreerrors': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                return "", None

            title = info.get('title', '')
            description = info.get('description', '')
            thumbnail = info.get('thumbnail')  # best quality thumbnail URL

            with tempfile.TemporaryDirectory() as tmpdir:
                ydl_opts['outtmpl'] = os.path.join(tmpdir, '%(id)s.%(ext)s')
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_temp:
                        ydl_temp.download([url])
                except Exception as e:
                    print(f"Warning: subtitle download failed ({e}), falling back to description.")

                sub_text = ""
                for file in os.listdir(tmpdir):
                    if file.endswith('.vtt') or file.endswith('.srt'):
                        with open(os.path.join(tmpdir, file), 'r', encoding='utf-8') as f:
                            for line in f.readlines():
                                if '-->' not in line and not line.strip().isdigit() and not line.startswith('WEBVTT'):
                                    sub_text += line.strip() + " "

                text = f"{title}\n\n{sub_text}" if sub_text else f"{title}\n\n{description}"
                return text, thumbnail

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
