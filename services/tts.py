import edge_tts
import asyncio
import os
import uuid

# Choose a voice. Xiaoxiao is a popular high-quality female voice.
# Other options: zh-CN-YunxiNeural (male), zh-CN-YunjianNeural (male), zh-CN-XiaoyiNeural (female)
VOICE = "zh-CN-XiaoxiaoNeural"

async def generate_audio(text: str, output_dir: str = "static/audio") -> str:
    """
    Generate MP3 from text using edge-tts.
    Returns the filename of the generated audio.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    filename = f"{uuid.uuid4().hex}.mp3"
    output_path = os.path.join(output_dir, filename)
    
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(output_path)
    
    return filename

def generate_audio_sync(text: str, output_dir: str = "static/audio") -> str:
    """Synchronous wrapper for generate_audio."""
    return asyncio.run(generate_audio(text, output_dir))
