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


def generate_daily_brief(articles: list[dict]) -> str | None:
    """Generate a daily review from a list of articles (title + summary).
    Returns a markdown string or None.
    """
    client = _get_client()
    if not client:
        return None

    items = "\n".join(
        f"{i+1}. 《{a['title']}》\n   {a.get('summary','')[:150]}"
        for i, a in enumerate(articles)
    )

    prompt = f"""以下是从知识库中随机抽取的{len(articles)}篇文章。请写一段今日知识回顾（300-500字），要求：
1. 找出这些文章之间潜在的联系或共同主题，哪怕联系并不明显。
2. 每篇文章提炼一个核心观点，用一两句话说清楚。
3. 结尾写一句今日思考：把这些知识综合起来，对目标用户（想改善生活、有创业意愿、关心世界变化的人）意味着什么？
4. 语气像是一个思维活跃的朋友在和你分享，口语化，有温度。
5. 直接输出正文，不要标题，不要"今日回顾"等前缀。

文章列表：
{items}
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个知识策展人，善于发现不同领域知识之间的联系。只输出正文。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"DeepSeek API Error (generate_daily_brief): {e}")
        return None


def extract_insights(text: str) -> dict | None:
    """Extract structured insights from article text.
    Returns {"core": str, "insights": [str, str, str], "counterintuitive": str} or None.
    """
    client = _get_client()
    if not client:
        return None

    prompt = """请从以下文章中提取结构化洞见，严格按照以下 JSON 格式输出，不要任何额外说明：

{
  "core": "一句话概括文章最核心的论点（20-40字）",
  "insights": [
    "关键洞见1（15-30字）",
    "关键洞见2（15-30字）",
    "关键洞见3（15-30字）"
  ],
  "counterintuitive": "文章中最反直觉或最令人意外的一个发现（20-40字）"
}

文章内容：
"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是内容分析专家。只输出合法的 JSON，不输出任何其他内容。"},
                {"role": "user", "content": prompt + text[:6000]},
            ],
            max_tokens=400,
            temperature=0.3,
        )
        import json
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        print(f"DeepSeek API Error (extract_insights): {e}")
        return None


def generate_social_post(text: str, format_type: str = "wechat", pain_point: str = "") -> str | None:
    """Generate social media content from article text.
    format_type: 'wechat' | 'insight' | 'list'
    """
    client = _get_client()
    if not client:
        return None

    pain_point_line = f"聚焦的痛点/角度：{pain_point}\n" if pain_point.strip() else ""

    if format_type == "insight":
        prompt = f"""从以下文章中提炼出一句最有冲击力、最反直觉或最发人深省的洞见。
{pain_point_line}要求：
1. 只输出一句话（20-60字），不加任何说明或前缀。
2. 语言有力，让人忍不住转发。
3. 不要是废话或显而易见的结论。

文章内容：
{text[:3000]}"""
        max_tokens = 100

    elif format_type == "list":
        prompt = f"""从以下文章中提炼3个关键洞见，写成适合公众号/小红书的清单体内容。
{pain_point_line}要求：
1. 标题用「关于X，你可能不知道的3件事」或类似句式，吸引点击。
2. 每条洞见一句话核心 + 2-3句展开，总长300-500字。
3. 语言口语化，有画面感，避免空话。
4. 直接输出正文，不加任何说明。

文章内容：
{text[:5000]}"""
        max_tokens = 1000

    else:  # wechat
        prompt = f"""将以下文章改写为一篇公众号短文。
{pain_point_line}要求：
1. 只聚焦一个痛点或一个核心观点，不要面面俱到。
2. 字数800-1500字。
3. 结构：开头用一个场景或问题钩住读者，核心观点展开，结尾留一个行动建议或反思问题。
4. 语言口语化，有温度，像在跟读者说话。
5. 直接输出正文（含标题），不加任何说明或前缀。

文章内容：
{text[:8000]}"""
        max_tokens = 3000

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是内容创作专家。只输出正文，不输出任何解释或说明。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.8,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"DeepSeek API Error (generate_social_post): {e}")
        return None


def suggest_pain_points(text: str) -> list[str]:
    """Return 5 concrete pain-point angles for social content, as a list."""
    client = _get_client()
    if not client:
        return []
    try:
        import json as _json
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是内容策略师。只输出合法JSON字符串数组，不输出其他任何内容。"},
                {"role": "user", "content": (
                    "从以下文章中提炼5个具体的用户痛点或应用角度，用于生成有针对性的社媒内容。\n"
                    "要求：每个痛点15-25字，描述具体场景或问题，角度各不相同。\n"
                    "直接输出JSON字符串数组，例如：[\"痛点1\", \"痛点2\"]\n\n"
                    f"文章内容：\n{text[:4000]}"
                )},
            ],
            max_tokens=300,
            temperature=0.7,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        arr = _json.loads(raw)
        return [s for s in arr if isinstance(s, str)][:5]
    except Exception as e:
        print(f"DeepSeek API Error (suggest_pain_points): {e}")
        return []


def batch_generate_cards(text: str, count: int = 4) -> list[str]:
    """Generate `count` distinct insight card texts from different angles."""
    client = _get_client()
    if not client:
        return []
    try:
        import json as _json
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是内容创作专家。只输出合法JSON字符串数组，不输出其他任何内容。"},
                {"role": "user", "content": (
                    f"从以下文章中提炼{count}个不同角度的精华洞见，每条适合做成传播卡片（25-50字）。\n"
                    "要求：角度各不相同，语言有力、反直觉或发人深省，每条独立完整。\n"
                    "直接输出JSON字符串数组，例如：[\"洞见1\", \"洞见2\"]\n\n"
                    f"文章内容：\n{text[:5000]}"
                )},
            ],
            max_tokens=600,
            temperature=0.8,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        arr = _json.loads(raw)
        return [s for s in arr if isinstance(s, str)][:count]
    except Exception as e:
        print(f"DeepSeek API Error (batch_generate_cards): {e}")
        return []


def refine_atomic_note(text: str) -> str | None:
    """Distill a highlighted passage into a single atomic note in Chinese."""
    client = _get_client()
    if not client:
        return None
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是知识管理助手。将用户提供的文本精炼为一条原子笔记：一个独立完整的知识点，20-60字，中文，直接输出，不加标题或前缀。"},
                {"role": "user", "content": text[:1000]},
            ],
            max_tokens=120,
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"DeepSeek API Error (refine_atomic_note): {e}")
        return None


def find_related_articles(current_title: str, highlights: list[str], candidates: list[dict]) -> list[dict]:
    """Return up to 5 related articles based on current article's highlights.
    candidates: list of {id, title, summary, tags}
    Returns: list of {id, title, reason}
    """
    client = _get_client()
    if not client or not candidates or not highlights:
        return []

    highlights_str = "\n".join(f"- {h[:120]}" for h in highlights[:10])
    cands_str = "\n".join(
        f"{i+1}. [{c['id']}] 《{c['title']}》: {(c.get('summary') or '')[:80]}"
        for i, c in enumerate(candidates[:20])
    )

    prompt = f"""当前文章：《{current_title}》
用户高亮的知识点：
{highlights_str}

候选文章（格式：序号. [id] 《标题》: 摘要）：
{cands_str}

从候选文章中选出最多5篇最相关的，以JSON数组返回：
[{{"id": "文章id", "reason": "关联原因（15字内）"}}]

只输出JSON，不要其他文字。"""

    try:
        import json as _json
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是知识图谱助手。只输出合法JSON数组。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.3,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        arr = _json.loads(raw)
        id_to_cand = {c['id']: c for c in candidates}
        result = []
        for item in arr:
            cand = id_to_cand.get(item.get('id', ''))
            if cand:
                result.append({'id': cand['id'], 'title': cand['title'], 'reason': item.get('reason', '')})
        return result[:5]
    except Exception as e:
        print(f"DeepSeek API Error (find_related_articles): {e}")
        return []


def translate_full_article(text: str) -> tuple[str, list[str]]:
    """Translate a full English article to Chinese using DeepSeek.
    Short texts (<=6000 chars) use a single call; longer texts are chunked.
    Returns (zh_text, tags).
    """
    SINGLE_CALL_LIMIT = 3000
    CHUNK_SIZE = 3000

    if len(text) <= SINGLE_CALL_LIMIT:
        result = literal_translate(text)
        if result:
            return result
        return text, []

    # Chunk and translate
    chunks = chunk_text(text, chunk_size=CHUNK_SIZE)
    total = len(chunks)
    parts = []
    for i, chunk in enumerate(chunks):
        translated = literal_translate_chunk(chunk, i, total)
        if translated:
            # Remove the "接下来，" transition prefix added by literal_translate_chunk
            if i > 0 and translated.startswith("接下来，"):
                translated = translated[4:]
            parts.append(translated)
        else:
            parts.append(chunk)  # fallback: keep original

    # Generate tags from title + first chunk
    tags = generate_tags("", chunks[0])
    return "\n\n".join(parts), tags


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
