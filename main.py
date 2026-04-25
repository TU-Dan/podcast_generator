import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from curl_cffi import requests as cffi_requests
from services.extractor import extract_content, download_youtube_audio
from services.llm import distill_and_translate, format_transcript, chunk_text, polish_chunk, detect_language, generate_tags, generate_title, CHUNK_THRESHOLD
from services.tts import generate_audio_sync
from services.rss import add_episode, clean_description
from services.db import init_db, add_article, update_tags, get_untagged_articles


def _retag_untagged():
    """Background thread: generate tags for articles that have none."""
    articles = get_untagged_articles()
    if not articles:
        return
    print(f"[retag] Found {len(articles)} untagged articles, generating tags...")
    for a in articles:
        path = a["article_md_path"].lstrip("/")
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            continue
        tags = generate_tags(a["title"], content[:2000])
        if tags:
            update_tags(a["id"], tags)
            print(f"[retag] {a['title'][:30]} → {tags}")
    print("[retag] Done.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    threading.Thread(target=_retag_untagged, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory job tracking
jobs: dict[str, dict] = {}


def update_job(job_id: str, status: str, message: str, file: dict = None):
    jobs[job_id]["status"] = status
    jobs[job_id]["message"] = message
    if file:
        jobs[job_id]["files"].append(file)


def sanitize_filename(title: str) -> str:
    safe = re.sub(r'[^\w\u4e00-\u9fff]', '_', title)
    safe = re.sub(r'_+', '_', safe).strip('_')
    return safe[:50]


def download_thumbnail(thumbnail_url: str) -> str | None:
    """Download remote thumbnail to static/images/. Returns local URL path."""
    import urllib.request
    os.makedirs("static/images", exist_ok=True)
    try:
        req = urllib.request.Request(
            thumbnail_url,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            content_type = resp.headers.get_content_type() or "image/jpeg"
            ext = "png" if "png" in content_type else "jpg"
            filename = f"{uuid.uuid4().hex}.{ext}"
            path = os.path.join("static/images", filename)
            with open(path, "wb") as f:
                f.write(resp.read())
        return f"/static/images/{filename}"
    except Exception as e:
        print(f"Failed to download thumbnail: {e}")
        return None


def save_transcript(title: str, text: str, source_type: str) -> str:
    """Save raw transcript as Markdown. Returns relative URL path."""
    os.makedirs("static/transcripts", exist_ok=True)
    lang = detect_language(text)
    lang_label = "中文" if lang == "zh" else "英文"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{sanitize_filename(title)}_逐字稿_{ts}.md"
    path = os.path.join("static/transcripts", filename)

    note = "" if lang == "zh" else "\n\n> 英文原稿，中文播客文稿见对应的文稿文件。"

    content = f"""# {title} — 原始逐字稿

**来源类型**: {source_type}
**处理时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**原文语言**: {lang_label}
{note}

---

{text}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"/static/transcripts/{filename}"


def save_script(title: str, script: str, part_label: str = "") -> str:
    """Save podcast script as Markdown. Returns relative URL path."""
    os.makedirs("static/scripts", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    part_suffix = f"_{part_label}" if part_label else ""
    filename = f"{sanitize_filename(title)}{part_suffix}_{ts}.md"
    path = os.path.join("static/scripts", filename)

    display_title = f"{title} {part_label}".strip()
    content = f"""# {display_title}

**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M")}

---

{script}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"/static/scripts/{filename}"


def process_content_task(job_id: str, source: str, source_type: str, title: str, base_url: str, voice: str, use_original_audio: bool = False):
    source_url = source if source_type in ("url", "youtube") else None

    try:
        # 1. Extract text + thumbnail
        update_job(job_id, "extracting", "正在提取内容...")
        text, thumbnail_url = extract_content(source, source_type)
        if not text:
            update_job(job_id, "error", "内容提取失败，请检查链接或文件。")
            return

        # Auto-generate title if not provided
        if not title.strip():
            update_job(job_id, "extracting", "正在生成标题...")
            title = generate_title(text)
            jobs[job_id]["title"] = title

        print(f"[{job_id}] Starting: {title}")

        # Download thumbnail if available
        episode_image = None
        if thumbnail_url:
            episode_image = download_thumbnail(thumbnail_url)

        # 2. Save transcript
        transcript_url = save_transcript(title, text, source_type)
        update_job(job_id, "processing", "正在保存逐字稿...", file={
            "label": "原始逐字稿",
            "url": transcript_url
        })

        # 3. LLM processing + audio
        if use_original_audio and source_type == "youtube":
            # Structure the transcript without losing content, then use original audio
            update_job(job_id, "processing", "正在整理文字稿...")
            result = format_transcript(text)
            if not result:
                update_job(job_id, "error", "LLM 处理失败，请检查 API Key。")
                return
            script, tags = result

            script_url = save_script(title, script)
            update_job(job_id, "generating_audio", "正在下载原始音频...", file={
                "label": "播客文稿",
                "url": script_url
            })
            audio_filename = download_youtube_audio(source)
            if not audio_filename:
                update_job(job_id, "error", "原始音频下载失败，请检查链接。")
                return
            audio_mime = "audio/mpeg"

            audio_path = os.path.join("static/audio", audio_filename)
            audio_url = f"{base_url}/static/audio/{audio_filename}"
            audio_length = os.path.getsize(audio_path)

            add_episode(
                title=title,
                description=clean_description(text[:300]) + "...",
                audio_filename=audio_filename,
                audio_length=audio_length,
                base_url=base_url,
                episode_image=episode_image,
                audio_mime=audio_mime,
            )
            article_id = add_article(
                title=title,
                source_url=source_url,
                source_type=source_type,
                summary=clean_description(text[:300]) + "...",
                article_md_path=script_url,
                transcript_path=transcript_url,
                audio_url=audio_url,
                audio_length=audio_length,
                image_url=episode_image,
                word_count=len(script),
            )
            if tags:
                update_tags(article_id, tags)

        elif len(text) <= CHUNK_THRESHOLD:
            update_job(job_id, "processing", "正在使用 DeepSeek 处理内容...")
            result = distill_and_translate(text)
            if not result:
                update_job(job_id, "error", "LLM 处理失败，请检查 API Key。")
                return
            script, tags = result

            script_url = save_script(title, script)
            update_job(job_id, "generating_audio", "正在生成音频...", file={
                "label": "播客文稿",
                "url": script_url
            })
            audio_filename = generate_audio_sync(script, voice=voice)
            audio_mime = "audio/mpeg"

            audio_path = os.path.join("static/audio", audio_filename)
            audio_url = f"{base_url}/static/audio/{audio_filename}"
            audio_length = os.path.getsize(audio_path)

            add_episode(
                title=title,
                description=clean_description(text[:300]) + "...",
                audio_filename=audio_filename,
                audio_length=audio_length,
                base_url=base_url,
                episode_image=episode_image,
                audio_mime=audio_mime,
            )
            article_id = add_article(
                title=title,
                source_url=source_url,
                source_type=source_type,
                summary=clean_description(text[:300]) + "...",
                article_md_path=script_url,
                transcript_path=transcript_url,
                audio_url=audio_url,
                audio_length=audio_length,
                image_url=episode_image,
                word_count=len(script),
            )
            if tags:
                update_tags(article_id, tags)
                print(f"[{job_id}] Tags: {tags}")

        else:
            chunks = chunk_text(text)
            total = len(chunks)
            update_job(job_id, "processing", f"内容较长，分为 {total} 段处理...")

            # Generate tags once from title + first chunk sample
            tags = generate_tags(title, chunks[0])
            if tags:
                print(f"[{job_id}] Tags (chunked): {tags}")

            article_ids = []
            for i, chunk in enumerate(chunks):
                part_label = f"第{i + 1}段_共{total}段"
                part_display = f"（第{i + 1}段/共{total}段）"
                part_title = f"{title} {part_display}"

                update_job(job_id, "processing", f"正在处理第 {i + 1}/{total} 段...")
                polished = polish_chunk(chunk, i, total)
                if not polished:
                    print(f"[{job_id}] Skipping part {i + 1} due to LLM error.")
                    continue

                script_url = save_script(title, polished, part_label)
                update_job(job_id, "generating_audio", f"正在生成第 {i + 1}/{total} 段音频...", file={
                    "label": f"播客文稿 {part_display}",
                    "url": script_url
                })

                audio_filename = generate_audio_sync(polished, voice=voice)
                audio_path = os.path.join("static/audio", audio_filename)
                audio_url = f"{base_url}/static/audio/{audio_filename}"
                audio_length = os.path.getsize(audio_path)

                add_episode(
                    title=part_title,
                    description=clean_description(chunk[:300]) + "...",
                    audio_filename=audio_filename,
                    audio_length=audio_length,
                    base_url=base_url,
                    episode_image=episode_image,
                )
                article_id = add_article(
                    title=part_title,
                    source_url=source_url,
                    source_type=source_type,
                    summary=clean_description(chunk[:300]) + "...",
                    article_md_path=script_url,
                    transcript_path=transcript_url,
                    audio_url=audio_url,
                    audio_length=audio_length,
                    image_url=episode_image,
                    word_count=len(polished),
                )
                article_ids.append(article_id)

            # Apply same tags to all parts
            if tags:
                for aid in article_ids:
                    update_tags(aid, tags)

        file_count = len(jobs[job_id]['files'])
        print(f"[{job_id}] Finished: {title}")

        # Auto-publish to GitHub Pages if configured
        if os.getenv("GITHUB_PAGES_URL"):
            update_job(job_id, "publishing", f"内容生成完成，正在发布到 GitHub Pages...")
            try:
                result = subprocess.run(
                    ["python3", "scripts/publish_to_pages.py"],
                    capture_output=True, text=True, timeout=120
                )
                if result.returncode == 0:
                    update_job(job_id, "done", f"全部完成！已发布到 GitHub Pages。")
                else:
                    update_job(job_id, "done", f"生成完成，但发布失败：{result.stderr or result.stdout}")
            except subprocess.TimeoutExpired:
                update_job(job_id, "done", "生成完成，发布超时，请手动发布。")
            except Exception as e:
                update_job(job_id, "done", f"生成完成，发布出错：{e}")
        else:
            update_job(job_id, "done", f"全部完成！共生成 {file_count} 个文件。")

    except Exception as e:
        print(f"[{job_id}] Error: {e}")
        update_job(job_id, "error", f"处理出错：{e}")
    finally:
        if source_type in ['pdf', 'txt'] and os.path.exists(source):
            try:
                os.remove(source)
            except:
                pass


# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def library(request: Request):
    return templates.TemplateResponse(request=request, name="library.html")


@app.get("/generate", response_class=HTMLResponse)
async def generate_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/tags")
async def api_tags():
    from services.db import list_all_tags
    return JSONResponse(list_all_tags())


@app.get("/api/articles")
async def api_articles(source_type: str = None, q: str = None, tags: str = None, limit: int = 100, offset: int = 0):
    from services.db import list_articles, count_by_type
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    articles = list_articles(source_type=source_type, query=q, tags=tag_list, limit=limit, offset=offset)
    counts = count_by_type()
    return JSONResponse({"articles": articles, "counts": counts})


@app.get("/article/{article_id}", response_class=HTMLResponse)
async def article_page(request: Request, article_id: str):
    from services.db import get_article
    article = get_article(article_id)
    if not article:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse(request=request, name="article.html", context={"article": article})


@app.get("/api/articles/{article_id}")
async def api_article(article_id: str):
    from services.db import get_article
    article = get_article(article_id)
    if not article:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(article)


@app.get("/api/articles/{article_id}/content")
async def get_article_content(article_id: str):
    from services.db import get_article
    article = get_article(article_id)
    if not article or not article.get("article_md_path"):
        return JSONResponse({"error": "not found"}, status_code=404)
    # article_md_path is like /static/scripts/foo.md — strip leading /
    file_path = article["article_md_path"].lstrip("/")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return JSONResponse({"content": content})
    except FileNotFoundError:
        return JSONResponse({"error": "file not found"}, status_code=404)


@app.put("/api/articles/{article_id}/tags")
async def save_article_tags(article_id: str, request: Request):
    from services.db import get_article, update_tags
    if not get_article(article_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await request.json()
    tags = body.get("tags", [])
    update_tags(article_id, tags)
    return JSONResponse({"ok": True})


@app.post("/api/articles/{article_id}/content")
async def save_article_content(article_id: str, request: Request):
    from services.db import get_article
    article = get_article(article_id)
    if not article or not article.get("article_md_path"):
        return JSONResponse({"error": "not found"}, status_code=404)
    file_path = article["article_md_path"].lstrip("/")
    body = await request.json()
    content = body.get("content", "")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/publish")
async def publish_to_pages():
    pages_url = os.getenv("GITHUB_PAGES_URL", "").rstrip("/")
    if not pages_url:
        return JSONResponse({"ok": False, "error": "GITHUB_PAGES_URL 未在 .env 中配置"}, status_code=400)
    try:
        result = subprocess.run(
            ["python3", "scripts/publish_to_pages.py"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return JSONResponse({"ok": False, "error": result.stderr or result.stdout})
        rss_url = f"{pages_url}/podcast.xml"
        return JSONResponse({"ok": True, "rss_url": rss_url, "log": result.stdout})
    except subprocess.TimeoutExpired:
        return JSONResponse({"ok": False, "error": "发布超时，请检查网络连接"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "not_found", "message": "任务不存在"})
    return JSONResponse(job)


@app.post("/generate/url")
async def generate_from_url(
    background_tasks: BackgroundTasks,
    request: Request,
    url: str = Form(...),
    title: str = Form(""),
    voice: str = Form("zh-CN-YunxiNeural"),
    use_original_audio: bool = Form(False),
):
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "pending", "message": "任务已提交，等待处理...", "files": []}
    source_type = 'youtube' if 'youtube.com' in url or 'youtu.be' in url else 'url'
    base_url = str(request.base_url).rstrip('/')
    background_tasks.add_task(process_content_task, job_id, url, source_type, title, base_url, voice, use_original_audio)
    return JSONResponse({"job_id": job_id})


@app.post("/generate/file")
async def generate_from_file(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    voice: str = Form("zh-CN-YunxiNeural")
):
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "pending", "message": "任务已提交，等待处理...", "files": []}
    source_type = 'pdf' if file.filename.lower().endswith('.pdf') else 'txt'
    base_url = str(request.base_url).rstrip('/')
    background_tasks.add_task(process_content_task, job_id, temp_path, source_type, title, base_url, voice)
    return JSONResponse({"job_id": job_id})
