import os
from openai import OpenAI
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

def distill_and_translate(text: str) -> str:
    """
    Use DeepSeek API to distill, summarize, and translate the text
    into a ~5000 character Chinese script suitable for a podcast.
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("Error: DEEPSEEK_API_KEY not found in environment variables.")
        return "抱歉，系统未配置大模型 API Key，无法完成文本处理。"
        
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    
    # Cap the input text to ~100,000 characters to avoid exceeding DeepSeek's context window
    if len(text) > 100000:
        text = text[:100000] + "\n...[由于长度限制，后续内容已截断]..."
        
    prompt = (
        "你是一个专业的播客文案编辑。请将以下文本进行核心内容提取、蒸馏和总结，"
        "将其转化为一篇内容丰富、逻辑连贯的中文播客讲稿（目标长度约在2000到5000字之间，视原文信息量而定）。\n"
        "要求：\n"
        "1. 必须全部使用中文输出。\n"
        "2. 语言要口语化、自然流畅，适合直接用于语音合成（TTS）朗读，不要包含Markdown格式（如加粗、标题符等），不要包含无法朗读的特殊符号。\n"
        "3. 提取原文的核心观点和精彩细节，去掉冗余的废话。\n"
        "4. 如果原文较短，请适度润色和展开；如果原文极长，请提炼精华，确保最终字数在5000字左右。\n\n"
        "原文内容如下：\n"
    )
    
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个专业的中文播客文案编辑。"},
                {"role": "user", "content": prompt + text}
            ],
            max_tokens=8000,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"DeepSeek API Error: {e}")
        return "抱歉，使用大模型处理文本时发生错误，请检查 API Key 或网络连接。"
