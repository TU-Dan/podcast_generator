import edge_tts
import asyncio
import os
import re
import uuid

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓 (女声·温柔)",
    "zh-CN-XiaoyiNeural": "晓伊 (女声·活泼)",
    "zh-CN-YunxiNeural": "云希 (男声·活泼)",
    "zh-CN-YunyangNeural": "云扬 (男声·专业)",
    "zh-CN-YunjianNeural": "云健 (男声·成熟)",
}

def clean_text_for_tts(text: str) -> str:
    """Remove markdown and symbols that TTS would read aloud."""
    # Remove bold/italic markers: **text** -> text, *text* -> text
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
    # Remove headings: ## Title -> Title
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Remove inline code: `code` -> code
    text = re.sub(r'`+([^`]*)`+', r'\1', text)
    # Remove horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    # Remove remaining stray asterisks and underscores used for emphasis
    text = re.sub(r'(?<!\w)[*_]+(?!\w)', '', text)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


async def generate_audio(text: str, voice: str = DEFAULT_VOICE, output_dir: str = "static/audio") -> str:
    """Generate MP3 from text using edge-tts. Returns the filename."""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    text = clean_text_for_tts(text)

    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = os.path.join(output_dir, filename)

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

    return filename


def generate_audio_sync(text: str, voice: str = DEFAULT_VOICE, output_dir: str = "static/audio") -> str:
    """Synchronous wrapper for generate_audio."""
    return asyncio.run(generate_audio(text, voice, output_dir))
