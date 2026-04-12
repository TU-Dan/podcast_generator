import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

CHUNK_THRESHOLD = 96000


def _get_client():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Error: DEEPSEEK_API_KEY not found in environment variables.")
        return None
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def detect_language(text: str) -> str:
    """Returns 'zh' if mostly Chinese, else 'en'."""
    sample = text[:3000]
    if not sample:
        return 'en'
    chinese_chars = sum(1 for c in sample if '\u4e00' <= c <= '\u9fff')
    return 'zh' if chinese_chars / len(sample) > 0.1 else 'en'


def chunk_text(text: str, chunk_size: int = 96000) -> list[str]:
    """Split text into chunks at sentence boundaries."""
    sentence_endings = {'。', '！', '？', '!', '?', '\n'}
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break

        boundary = end
        for i in range(end, min(end + 200, len(text))):
            if text[i] in sentence_endings:
                boundary = i + 1
                break

        chunks.append(text[start:boundary])
        start = boundary

    return [c for c in chunks if c.strip()]


_META_PATTERNS = [
    r'^好的[，,].*\n?',
    r'^收到[，,。].*\n?',
    r'^作为.{0,20}编辑.*\n?',
    r'^我[将会]为你.*\n?',
    r'^我[将会]帮你.*\n?',
    r'^以下是.*播客.*\n?',
    r'^下面是.*播客.*\n?',
    r'^接下来[，,]我[将会].*\n?',
]

def _strip_meta(text: str) -> str:
    """Remove LLM self-introduction lines from the start of output."""
    import re
    for pattern in _META_PATTERNS:
        text = re.sub(pattern, '', text, flags=re.MULTILINE)
    return text.lstrip()


def polish_chunk(chunk: str, part_index: int, total_parts: int) -> str | None:
    """Polish a text chunk for podcast. Preserves all content. Returns Markdown."""
    client = _get_client()
    if not client:
        return None

    prompt = f"""这是一段较长内容的第{part_index + 1}部分（共{total_parts}部分）。
请将以下文本逐句翻译润色为适合朗读的中文播客讲稿，要求：
1. 必须全部使用中文输出。
2. 如果原文是英文，逐段翻译为中文；如果已是中文，直接润色。
3. 直接输出播客内容，不要写任何确认语、任务说明或角色介绍（不要出现"好的""收到""作为编辑""我将处理"等字眼）。
4. 语言口语化、自然流畅。
5. 保留原文的每一个观点、细节、例子和数据，内容覆盖率要达到95%以上，绝不省略信息。
6. 只去除字幕中明显的重复句子和无意义填充词（如嗯、呃、you know 等），其余全部保留。
7. 使用 Markdown 格式组织内容（可用二级标题划分主题、加粗重点），便于阅读和存档。
8. 输出字数应不少于原文字数的80%。

原文内容如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是播客文案编辑。只输出播客正文，绝不输出任何确认语、任务说明或角色介绍。"},
                {"role": "user", "content": prompt + chunk}
            ],
            max_tokens=8192,
            temperature=0.7
        )
        result = _strip_meta(response.choices[0].message.content)
        # Prepend transition phrase for non-first parts
        if part_index > 0:
            result = f"接下来，{result}"
        return result
    except Exception as e:
        print(f"DeepSeek API Error (part {part_index + 1}): {e}")
        return None


def distill_and_translate(text: str) -> str | None:
    """Distill and translate text into a Chinese podcast script. Returns Markdown."""
    client = _get_client()
    if not client:
        return None

    prompt = """请将以下文本转化为适合朗读的中文播客讲稿，要求：
1. 必须全部使用中文输出。
2. 如果原文是英文，逐段翻译为中文；如果已是中文，直接润色。
3. 直接输出播客内容，不要写任何确认语、任务说明或角色介绍（不要出现"好的""收到""作为编辑""我将处理"等字眼）。
4. 语言要口语化、自然流畅。
5. 保留原文所有核心观点、具体例子、数据和细节，内容覆盖率达到90%以上，不要过度精简。
6. 只去除明显的废话、重复和无意义填充词，其余内容全部保留。
7. 使用 Markdown 格式组织内容（可用二级标题划分主题、加粗重点），便于阅读和存档。
8. 输出字数应不少于原文字数的70%。

原文内容如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是播客文案编辑。只输出播客正文，绝不输出任何确认语、任务说明或角色介绍。"},
                {"role": "user", "content": prompt + text}
            ],
            max_tokens=8192,
            temperature=0.7
        )
        return _strip_meta(response.choices[0].message.content)
    except Exception as e:
        print(f"DeepSeek API Error: {e}")
        return None
