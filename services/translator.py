from deep_translator import GoogleTranslator
import re

def split_text(text: str, max_length: int = 4000) -> list[str]:
    """
    Split text into chunks of maximum length `max_length`.
    Tries to split by paragraphs or sentences to avoid breaking words.
    """
    if len(text) <= max_length:
        return [text]
        
    chunks = []
    current_chunk = ""
    
    # Split by paragraphs first
    paragraphs = text.split('\n')
    
    for para in paragraphs:
        if len(current_chunk) + len(para) + 1 <= max_length:
            current_chunk += para + '\n'
        else:
            # If a single paragraph is too long, split by sentences
            if len(para) > max_length:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) + 1 <= max_length:
                        current_chunk += sentence + ' '
                    else:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = sentence + ' '
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + '\n'
                
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks

def translate_to_chinese(text: str) -> str:
    """
    Translate text to Chinese using deep-translator.
    Handles long text by splitting it into chunks.
    """
    if not text or not text.strip():
        return ""
        
    translator = GoogleTranslator(source='auto', target='zh-CN')
    
    # Limit text to first 5000 characters to prevent extremely long processing
    # and potential rate limits during translation
    if len(text) > 5000:
        text = text[:5000] + "..."
        
    chunks = split_text(text)
    translated_chunks = []
    
    for chunk in chunks:
        try:
            translated = translator.translate(chunk)
            if translated:
                translated_chunks.append(translated)
        except Exception as e:
            print(f"Translation error: {e}")
            # Fallback to original text if translation fails
            translated_chunks.append(chunk)
            
    return "\n\n".join(translated_chunks)
