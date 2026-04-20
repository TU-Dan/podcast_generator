import os
import re
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

CHUNK_THRESHOLD = 80000


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


def chunk_text(text: str, chunk_size: int = 80000) -> list[str]:
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
请将以下文本转化为适合朗读的中文播客讲稿，要求：
1. 必须全部使用中文输出。
2. 如果原文是英文，请翻译为中文；如果已是中文，直接润色。
3. 直接输出播客内容，不要写任何确认语、任务说明或角色介绍（不要出现"好的""收到""作为编辑""我将处理"等字眼）。
4. 语言口语化、自然流畅。
5. 保留原文所有观点、细节、数据，不压缩、不省略内容。
6. 去除字幕中明显的重复句子和无意义填充词（如嗯、呃、you know 等）。
7. 使用 Markdown 格式组织内容（可用二级标题划分主题、加粗重点），便于阅读和存档。

原文内容如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是播客文案编辑。只输出播客正文，绝不输出任何确认语、任务说明或角色介绍。"},
                {"role": "user", "content": prompt + chunk}
            ],
            max_tokens=8000,
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


def _parse_tags(raw: str) -> tuple[str, list[str]]:
    """Split LLM output into (script, tags). Tags follow a '---TAGS---' marker."""
    marker = "---TAGS---"
    idx = raw.rfind(marker)
    if idx == -1:
        return raw.strip(), []
    script = raw[:idx].strip()
    tag_line = raw[idx + len(marker):].strip()
    # Accept comma or Chinese comma separated, strip whitespace and #
    tags = [t.strip().lstrip("#").strip() for t in re.split(r'[,，]', tag_line) if t.strip()]
    return script, tags[:8]  # cap at 8 tags


def literal_translate(text: str) -> tuple[str, list[str]] | None:
    """English (or non-Chinese primary) → Chinese literal translation only.
    No summarization, no omission. Returns (script_markdown, tags) or None."""
    client = _get_client()
    if not client:
        return None

    prompt = """请将以下全文逐句直译为中文播客朗读稿。
要求：
1. 只翻译与转写，不做摘要、不提炼观点、不删减段落。
2. 保持原文顺序与信息完整，专有名词可保留英文或常见中文译名。
3. 语言通顺可读即可，不要改写为「讲稿风格」的二次创作。
4. 全部使用中文输出（原文中的代码、URL、公式可保留原样）。
5. 使用 Markdown 分段（必要时用小标题），便于存档。
6. 直接输出正文，不要任何确认语或角色介绍。
7. 在正文末尾另起一行输出标签（3到6个关键词，逗号分隔）：
---TAGS---
标签1, 标签2, 标签3

原文如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是专业翻译。只输出直译后的中文正文和末尾标签行，不输出任何其他说明。",
                },
                {"role": "user", "content": prompt + text},
            ],
            max_tokens=8000,
            temperature=0.3,
        )
        raw = _strip_meta(response.choices[0].message.content)
        return _parse_tags(raw)
    except Exception as e:
        print(f"DeepSeek API Error (literal_translate): {e}")
        return None


def literal_translate_chunk(chunk: str, part_index: int, total_parts: int) -> str | None:
    """Long text: translate one chunk literally. No tags in chunk output."""
    client = _get_client()
    if not client:
        return None

    prompt = f"""这是长文第{part_index + 1}部分（共{total_parts}部分）。请将该部分逐句直译为中文。
要求：
1. 只翻译，不摘要、不合并段落、不删减。
2. 保持本段内部顺序与信息完整。
3. 全部使用中文（代码、URL 可保留）。
4. 使用 Markdown 分段。
5. 直接输出译文，不要任何确认语或角色介绍。

原文如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "你是专业翻译。只输出本段直译正文，不输出任何其他说明。",
                },
                {"role": "user", "content": prompt + chunk},
            ],
            max_tokens=8000,
            temperature=0.3,
        )
        result = _strip_meta(response.choices[0].message.content)
        if part_index > 0:
            result = f"接下来，{result}"
        return result
    except Exception as e:
        print(f"DeepSeek API Error (literal chunk {part_index + 1}): {e}")
        return None


def distill_and_translate(text: str) -> tuple[str, list[str]] | None:
    """Distill and translate text into a Chinese podcast script.
    Returns (script_markdown, tags) or None on failure."""
    client = _get_client()
    if not client:
        return None

    prompt = """请将以下文本进行核心内容提取、蒸馏和总结，
转化为一篇内容丰富、逻辑连贯的中文播客讲稿（目标长度约在2000到5000字之间，视原文信息量而定）。
要求：
1. 必须全部使用中文输出。
2. 直接输出播客内容，不要写任何确认语、任务说明或角色介绍（不要出现"好的""收到""作为编辑""我将处理"等字眼）。
3. 语言要口语化、自然流畅。
4. 提取原文的核心观点和精彩细节，去掉冗余的废话。
5. 如果原文较短，请适度润色和展开；如果原文极长，请提炼精华，确保最终字数在5000字左右。
6. 使用 Markdown 格式组织内容（可用二级标题划分主题、加粗重点），便于阅读和存档。
7. 在正文末尾另起一行，输出如下格式的标签行（3到6个关键词，逗号分隔，代表文章的核心主题）：
---TAGS---
标签1, 标签2, 标签3

原文内容如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是播客文案编辑。只输出播客正文和末尾的标签行，绝不输出任何确认语、任务说明或角色介绍。"},
                {"role": "user", "content": prompt + text}
            ],
            max_tokens=8000,
            temperature=0.7
        )
        raw = _strip_meta(response.choices[0].message.content)
        return _parse_tags(raw)
    except Exception as e:
        print(f"DeepSeek API Error: {e}")
        return None


def format_transcript(text: str) -> tuple[str, list[str]] | None:
    """Structure raw transcript into clean markdown without losing any content.
    Returns (markdown, tags) or None on failure."""
    client = _get_client()
    if not client:
        return None

    prompt = """请将以下原始文字稿整理为结构清晰的中文 Markdown 文档。

要求：
1. 保留所有信息，不删减、不压缩任何观点、细节或数据。
2. 如果原文是英文，请翻译为中文，但确保内容完整无遗漏。
3. 去除字幕中明显的重复句和无意义填充词（如"嗯""呃""you know"等）。
4. 用二级标题（##）按主题分段，加粗关键术语。
5. 直接输出正文，不要写任何确认语或角色介绍。
6. 在正文末尾另起一行，输出标签行（3到6个关键词，逗号分隔）：
---TAGS---
标签1, 标签2, 标签3

原文内容如下：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是专业文字编辑。只输出整理后的正文和末尾的标签行，不输出任何其他内容。"},
                {"role": "user", "content": prompt + text},
            ],
            max_tokens=8000,
            temperature=0.3,
        )
        raw = _strip_meta(response.choices[0].message.content)
        return _parse_tags(raw)
    except Exception as e:
        print(f"DeepSeek API Error (format_transcript): {e}")
        return None


def generate_title(text: str) -> str:
    """Generate a concise Chinese title from content. Falls back gracefully."""
    # Try first # heading in text
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()[:40]

    client = _get_client()
    if not client:
        return "未命名播客"
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是标题生成器。只输出标题本身，不超过20个字，不加引号、序号或任何前缀。"},
                {"role": "user", "content": f"根据以下内容生成一个简洁的中文播客标题（不超过20字）：\n{text[:1500]}"},
            ],
            max_tokens=40,
            temperature=0.5,
        )
        return response.choices[0].message.content.strip().strip('"').strip("'")[:40]
    except Exception as e:
        print(f"Title generation error: {e}")
        return "未命名播客"


def generate_tags(title: str, text_sample: str) -> list[str]:
    """Lightweight call to generate tags for long chunked content."""
    client = _get_client()
    if not client:
        return []
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是内容分类助手。只输出标签，逗号分隔，不输出其他任何内容。"},
                {"role": "user", "content": f"根据以下标题和内容片段，给出3到6个中文关键词标签（逗号分隔）：\n标题：{title}\n内容：{text_sample[:2000]}"}
            ],
            max_tokens=60,
            temperature=0.3
        )
        raw = response.choices[0].message.content.strip()
        return [t.strip().lstrip("#").strip() for t in re.split(r'[,，]', raw) if t.strip()][:8]
    except Exception as e:
        print(f"DeepSeek tag generation error: {e}")
        return []
