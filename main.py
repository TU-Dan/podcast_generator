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
from services.extractor import extract_content
from services.llm import distill_and_translate, chunk_text, polish_chunk, detect_language, CHUNK_THRESHOLD
from services.tts import generate_audio_sync
from services.rss import add_episode, clean_description
from services.importer import import_from_rss

app = FastAPI()

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
    os.makedirs("static/images", exist_ok=True)
    try:
        r = cffi_requests.get(thumbnail_url, timeout=5, impersonate="chrome")
        r.raise_for_status()
        content_type = r.headers.get("content-type", "image/jpeg").lower()
        ext = "png" if "png" in content_type else "jpg"
        filename = f"{uuid.uuid4().hex}.{ext}"
        path = os.path.join("static/images", filename)
        with open(path, "wb") as f:
            f.write(r.content)
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


def process_content_task(job_id: str, source: str, source_type: str, title: str, base_url: str, voice: str):
    print(f"[{job_id}] Starting: {title}")

    try:
        # 1. Extract text + thumbnail
        update_job(job_id, "extracting", "正在提取内容...")
        text, thumbnail_url = extract_content(source, source_type)
        if not text:
            update_job(job_id, "error", "内容提取失败，请检查链接或文件。")
            return

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

        # 3. LLM + TTS
        if len(text) <= CHUNK_THRESHOLD:
            update_job(job_id, "processing", "正在使用 DeepSeek 处理内容...")
            script = distill_and_translate(text)
            if not script:
                update_job(job_id, "error", "LLM 处理失败，请检查 API Key。")
                return

            script_url = save_script(title, script)
            update_job(job_id, "generating_audio", "正在生成音频...", file={
                "label": "播客文稿",
                "url": script_url
            })

            audio_filename = generate_audio_sync(script, voice=voice)
            audio_path = os.path.join("static/audio", audio_filename)
            add_episode(
                title=title,
                description=clean_description(text[:300]) + "...",
                audio_filename=audio_filename,
                audio_length=os.path.getsize(audio_path),
                base_url=base_url,
                episode_image=episode_image
            )

        else:
            chunks = chunk_text(text)
            total = len(chunks)
            update_job(job_id, "processing", f"内容较长，分为 {total} 段处理...")

            for i, chunk in enumerate(chunks):
                part_label = f"第{i + 1}段_共{total}段"
                part_display = f"（第{i + 1}段/共{total}段）"

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
                add_episode(
                    title=f"{title} {part_display}",
                    description=clean_description(chunk[:300]) + "...",
                    audio_filename=audio_filename,
                    audio_length=os.path.getsize(audio_path),
                    base_url=base_url,
                    episode_image=episode_image
                )

        update_job(job_id, "done", f"全部完成！共生成 {len(jobs[job_id]['files'])} 个文件。")
        print(f"[{job_id}] Finished: {title}")

    except Exception as e:
        print(f"[{job_id}] Error: {e}")
        update_job(job_id, "error", f"处理出错：{e}")
    finally:
        if source_type in ['pdf', 'txt'] and os.path.exists(source):
            try:
                os.remove(source)
            except:
                pass


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/import-rss")
async def import_rss(request: Request, rss_url: str = Form(...)):
    base_url = str(request.base_url).rstrip("/")
    try:
        imported, skipped = import_from_rss(rss_url, base_url)
        return JSONResponse({"ok": True, "imported": imported, "skipped": skipped})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


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
    title: str = Form(...),
    voice: str = Form("zh-CN-XiaoxiaoNeural")
):
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"status": "pending", "message": "任务已提交，等待处理...", "files": []}
    source_type = 'youtube' if 'youtube.com' in url or 'youtu.be' in url else 'url'
    base_url = str(request.base_url).rstrip('/')
    background_tasks.add_task(process_content_task, job_id, url, source_type, title, base_url, voice)
    return JSONResponse({"job_id": job_id})


@app.post("/generate/file")
async def generate_from_file(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...),
    voice: str = Form("zh-CN-XiaoxiaoNeural")
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
