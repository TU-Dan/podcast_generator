from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import shutil
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from services.extractor import extract_content
from services.llm import distill_and_translate
from services.tts import generate_audio_sync
from services.rss import add_episode

app = FastAPI()

# Mount static files for audio and rss
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup templates
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

def process_content_task(source: str, source_type: str, title: str, base_url: str):
    print(f"Starting to process: {title}")
    
    try:
        # 1. Extract
        print("Extracting content...")
        text = extract_content(source, source_type)
        if not text:
            print("Failed to extract content.")
            return
            
        # 2. Distill and Translate using DeepSeek LLM
        print("Distilling and translating content via DeepSeek...")
        translated_text = distill_and_translate(text)
        
        # 3. TTS
        print("Generating audio...")
        audio_filename = generate_audio_sync(translated_text)
        
        # 4. Update RSS
        print("Updating RSS...")
        audio_path = os.path.join("static/audio", audio_filename)
        audio_length = os.path.getsize(audio_path)
        
        add_episode(
            title=title,
            description=text[:200] + "...",
            audio_filename=audio_filename,
            audio_length=audio_length,
            base_url=base_url
        )
        print(f"Finished processing: {title}")
    except Exception as e:
        print(f"Error processing {title}: {e}")
    finally:
        # Clean up temp file if it was uploaded
        if source_type in ['pdf', 'txt'] and os.path.exists(source):
            try:
                os.remove(source)
            except:
                pass

@app.post("/generate/url")
async def generate_from_url(
    background_tasks: BackgroundTasks,
    request: Request,
    url: str = Form(...),
    title: str = Form(...)
):
    source_type = 'youtube' if 'youtube.com' in url or 'youtu.be' in url else 'url'
    base_url = str(request.base_url).rstrip('/')
    
    background_tasks.add_task(process_content_task, url, source_type, title, base_url)
    
    return {"message": "Task started in background. Check your RSS feed later."}

@app.post("/generate/file")
async def generate_from_file(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(...)
):
    # Save uploaded file temporarily
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, file.filename)
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    source_type = 'pdf' if file.filename.lower().endswith('.pdf') else 'txt'
    base_url = str(request.base_url).rstrip('/')
    
    background_tasks.add_task(process_content_task, temp_path, source_type, title, base_url)
    
    return {"message": "Task started in background. Check your RSS feed later."}
