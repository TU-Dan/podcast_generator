import edge_tts
import asyncio
import os
import re
import uuid

DEFAULT_VOICE = "zh-CN-YunxiNeural"

VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓 (女声·温柔)",
    "zh-CN-XiaoyiNeural": "晓伊 (女声·活泼)",
    "zh-CN-YunxiNeural": "云希 (男声·活泼)",
    "zh-CN-YunyangNeural": "云扬 (男声·专业)",
    "zh-CN-YunjianNeural": "云健 (男声·成熟)",
}

# edge-tts struggles with very long inputs; chunk at this many chars
TTS_CHUNK_SIZE = 2000


def clean_text_for_tts(text: str) -> str:
    """Remove markdown and symbols that TTS would read aloud."""
    # Remove images: ![alt](url) -> ''
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    # Remove markdown links: [text](url) -> text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)
    # Remove bold/italic (single-line only, no DOTALL to avoid eating paragraphs)
    text = re.sub(r'\*{1,3}([^\n*]*?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}([^\n_]*?)_{1,2}', r'\1', text)
    # Remove headings: ## Title -> Title (with or without space after #)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Remove inline code: `code` -> code
    text = re.sub(r'`+([^`\n]*)`+', r'\1', text)
    # Remove horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
    # Remove table separators like |---|---|
    text = re.sub(r'^\|[-| :]+\|$', '', text, flags=re.MULTILINE)
    # Remove table pipes (keep cell content)
    text = re.sub(r'\|', ' ', text)
    # Remove remaining stray asterisks and # sequences
    text = re.sub(r'\*+', '', text)
    text = re.sub(r'#{2,}', '', text)
    text = re.sub(r'(?<![A-Za-z])#(?![A-Za-z0-9])', '', text)

    # -- Characters that crash edge-tts when isolated --
    # Replace Chinese ellipsis / em-dash with comma (speakable pause)
    text = text.replace('\u2026', ',')      # … → ,
    text = text.replace('\u2014', ',')      # — → ,
    text = text.replace('\u2013', ',')      # – → ,
    # Replace arrows and bullets with empty
    text = re.sub(r'[\u2190-\u21ff]', '', text)    # arrows ← → ↑ ↓ etc.
    text = re.sub(r'[\u2022\u2605\u2606\u25cf\u25cb\u25a0\u25a1]', '', text)  # bullets/stars/squares
    # Replace ... with comma
    text = re.sub(r'\.{2,}', ',', text)
    # Replace repeated punctuation like 。。。 with single
    text = re.sub(r'([。！？!?])\1+', r'\1', text)
    # Remove tilde ～
    text = text.replace('\uff5e', '')
    # Remove middot · (not speakable alone)
    text = text.replace('\u00b7', ' ')
    # Remove smart quotes (not speakable alone)
    text = re.sub(r'[\u201c\u201d\u2018\u2019]', '', text)

    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Collapse multiple commas/spaces
    text = re.sub(r'[,，]{2,}', '，', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def _has_speech_content(text: str) -> bool:
    """Return True if text has at least one letter or digit (not just punctuation/whitespace)."""
    return bool(re.search(r'[\w\u4e00-\u9fff]', text))


def _split_for_tts(text: str, max_chars: int = TTS_CHUNK_SIZE) -> list[str]:
    """Split text into chunks at sentence boundaries for edge-tts."""
    sentences = re.split(r'(?<=[。！？!?\n])', text)
    chunks, current = [], ''
    for s in sentences:
        if len(current) + len(s) > max_chars and current:
            chunks.append(current.strip())
            current = s
        else:
            current += s
    if current.strip():
        chunks.append(current.strip())
    # Filter out chunks with no actual speech content (punctuation-only fragments)
    return [c for c in chunks if _has_speech_content(c)] or ([text] if _has_speech_content(text) else [])


async def generate_audio(text: str, voice: str = DEFAULT_VOICE, output_dir: str = "static/audio") -> str:
    """Generate MP3 from text using edge-tts. Returns the filename."""
    os.makedirs(output_dir, exist_ok=True)

    text = clean_text_for_tts(text)
    if not text:
        raise ValueError("Text is empty after cleaning — nothing to synthesize.")

    print(f"[TTS] {len(text)} chars, voice={voice}")

    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = os.path.join(output_dir, filename)

    chunks = _split_for_tts(text)
    if not chunks:
        raise ValueError("No speakable content found after cleaning.")

    print(f"[TTS] {len(chunks)} chunk(s), sizes: {[len(c) for c in chunks]}")

    async def synth_chunk(chunk: str, out: str) -> bool:
        """Synthesize one chunk with retries. Returns True on success."""
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(chunk, voice)
                await communicate.save(out)
                # Verify non-empty output
                if os.path.getsize(out) > 0:
                    return True
                print(f"[TTS] Chunk produced empty file, attempt {attempt + 1}/3")
            except Exception as e:
                print(f"[TTS] Chunk failed (attempt {attempt + 1}/3): {e}")
            await asyncio.sleep(1 + attempt)
        # Log the problematic chunk for debugging
        print(f"[TTS] Giving up on chunk ({len(chunk)} chars): {chunk[:80]!r}...")
        return False

    if len(chunks) == 1:
        ok = await synth_chunk(chunks[0], output_path)
        if not ok:
            raise RuntimeError(f"TTS failed after retries ({len(chunks[0])} chars)")
    else:
        parts = []
        for i, chunk in enumerate(chunks):
            tmp_path = os.path.join(output_dir, f"_tmp_{uuid.uuid4().hex}.mp3")
            ok = await synth_chunk(chunk, tmp_path)
            if ok:
                with open(tmp_path, 'rb') as f:
                    parts.append(f.read())
                os.remove(tmp_path)
            else:
                # Skip this chunk rather than crash the whole job
                print(f"[TTS] Skipping chunk {i + 1}/{len(chunks)}")
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
        if not parts:
            raise RuntimeError("TTS failed: all chunks produced no audio")
        with open(output_path, 'wb') as f:
            for part in parts:
                f.write(part)

    return filename


def generate_audio_sync(text: str, voice: str = DEFAULT_VOICE, output_dir: str = "static/audio") -> str:
    """Synchronous wrapper for generate_audio."""
    return asyncio.run(generate_audio(text, voice, output_dir))
