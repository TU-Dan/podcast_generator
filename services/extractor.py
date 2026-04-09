import os
import tempfile
import yt_dlp
import trafilatura
from PyPDF2 import PdfReader

def extract_from_url(url: str) -> str:
    """Extract text from a general web page."""
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        text = trafilatura.extract(downloaded)
        return text if text else ""
    return ""

def extract_from_youtube(url: str) -> str:
    """Extract subtitles from a YouTube video."""
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en', 'zh-Hans', 'zh-Hant', 'zh'],
        'outtmpl': '%(id)s.%(ext)s',
        'quiet': True,
        'ignoreerrors': True, # Ignore errors like 429 for subtitles
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                return ""
                
            title = info.get('title', '')
            description = info.get('description', '')
            
            # To actually get subtitle text, we'd need to download it. Let's do a quick download to a temp dir.
            with tempfile.TemporaryDirectory() as tmpdir:
                ydl_opts['outtmpl'] = os.path.join(tmpdir, '%(id)s.%(ext)s')
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_temp:
                        ydl_temp.download([url])
                except Exception as download_e:
                    print(f"Warning: Subtitle download failed ({download_e}), falling back to description.")
                    
                # Find the downloaded subtitle file
                sub_text = ""
                for file in os.listdir(tmpdir):
                    if file.endswith('.vtt') or file.endswith('.srt'):
                        with open(os.path.join(tmpdir, file), 'r', encoding='utf-8') as f:
                            # Very basic VTT/SRT parsing (just stripping timestamps)
                            lines = f.readlines()
                            for line in lines:
                                if not '-->' in line and not line.strip().isdigit() and not line.startswith('WEBVTT'):
                                    sub_text += line.strip() + " "
                
                if sub_text:
                    return f"{title}\n\n{sub_text}"
                
            return f"{title}\n\n{description}"
        except Exception as e:
            print(f"Error extracting from YouTube: {e}")
            return ""

def extract_from_pdf(file_path: str) -> str:
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
    return text

def extract_from_txt(file_path: str) -> str:
    """Extract text from a TXT file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"Error reading TXT: {e}")
        return ""

def extract_content(source: str, source_type: str = 'url') -> str:
    """
    Main entry point for extraction.
    source_type can be 'url', 'youtube', 'pdf', 'txt'
    """
    if source_type == 'youtube' or ('youtube.com' in source or 'youtu.be' in source):
        return extract_from_youtube(source)
    elif source_type == 'url' or source.startswith('http'):
        return extract_from_url(source)
    elif source_type == 'pdf':
        return extract_from_pdf(source)
    elif source_type == 'txt':
        return extract_from_txt(source)
    else:
        return ""
