from fastapi import FastAPI, HTTPException, BackgroundTasks, Security, Depends
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi import UploadFile, File
from starlette.status import HTTP_403_FORBIDDEN
from pydantic import BaseModel, Field
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional, Dict, List
import json
import shutil
import os
import threading
import queue
import comfy_client
import traceback
import requests
import subprocess
import time

load_dotenv()
app = FastAPI()

# --- 1. CONFIGURATION & PATHS ---
IS_CLOUD = os.getenv("RENDER") is not None
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

INPUTS_DIR = REPO_ROOT / "inputs" / "videos"

if IS_CLOUD:
    STORAGE_ROOT = Path("/tmp")
    OUTPUTS_DIR = STORAGE_ROOT / "outputs"
    ASSETS_DIR = STORAGE_ROOT / "assets"
else:
    OUTPUTS_DIR = REPO_ROOT / "outputs"
    ASSETS_DIR = REPO_ROOT / "assets"

for d in [OUTPUTS_DIR, ASSETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Automatically copy default assets from your GitHub repo to the /tmp folder on startup
if IS_CLOUD:
    repo_assets = REPO_ROOT / "assets"
    if repo_assets.exists():
        for item in repo_assets.iterdir():
            if item.is_file() and not (ASSETS_DIR / item.name).exists():
                shutil.copy2(item, ASSETS_DIR / item.name)

print(f"==== BACKEND STARTED ({'CLOUD' if IS_CLOUD else 'LOCAL'}) ====", flush=True)

# --- 2. SECURITY ---
API_KEY_NAME = "X-Access-Token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def get_api_key(api_key_header: str = Security(api_key_header)):
    if not IS_CLOUD: return "dev-mode"
    allowed_keys = os.getenv("ALLOWED_KEYS", "").split(",")
    if api_key_header in allowed_keys: return api_key_header
    raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid Access Token")

# --- 3. CORS & STATIC ---
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/inputs", StaticFiles(directory=INPUTS_DIR), name="inputs")
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# --- 4. MODELS ---
class ClipAction(BaseModel):
    pass_num: int = Field(alias="pass")
    character: str
    mask: str = "AUTO"
    class Config: allow_population_by_field_name = True

class ClipInfo(BaseModel):
    clip_id: str
    path: str
    start: int; end: int; type: str
    actions: List[ClipAction]
    status: str = "pending"
    mask_points: Optional[Dict] = None

# --- 5. JOB QUEUE ---
JOB_QUEUE = queue.Queue()
stop_event = threading.Event()
processing_status = {
    "is_processing": False, "current_clip": None, 
    "current_pass": 0, "queue_size": 0, "last_completed": None
}

def worker_loop():
    print("[WORKER] Thread started...", flush=True)
    while True:
        task = JOB_QUEUE.get()
        if task is None: break
        video_id, clip_ids = task
        try:
            processing_status["queue_size"] = JOB_QUEUE.qsize()
            run_queue_processor(video_id, clip_ids)
        except Exception as e:
            print(f"[WORKER ERROR] {e}", flush=True)
            traceback.print_exc()
        finally:
            JOB_QUEUE.task_done()
            processing_status["queue_size"] = JOB_QUEUE.qsize()

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()

# --- 6. HELPERS ---
def get_job_profile_path(video_id: str) -> Path:
    path = INPUTS_DIR / video_id / f"{video_id}.job.json"
    if not path.exists(): path = INPUTS_DIR / f"{video_id}.job.json"
    return path

def get_mask_path(video_id: str, clip_id: str, pass_num: int) -> Path:
    mask_dir = OUTPUTS_DIR / video_id / "masks"
    mask_dir.mkdir(parents=True, exist_ok=True)
    return mask_dir / f"{clip_id}_pass{pass_num}.json"

def move_comfy_output(remote_filename: str, dest_path: Path) -> bool:
    from comfy_client import SERVER_ADDRESS, HTTP_PROTO, get_auth_header
    url = f"{HTTP_PROTO}://{SERVER_ADDRESS}/view?filename={remote_filename}&subfolder=&type=output"
    for attempt in range(5):
        if stop_event.is_set(): return False
        try:
            r = requests.get(url, headers=get_auth_header(), stream=True, timeout=30)
            if r.status_code == 200:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                return True
        except Exception as e: pass
        time.sleep(2)
    return False

# --- 7. PROCESSOR ---
def run_queue_processor(video_id: str, clip_ids: List[str]):
    global processing_status
    processing_status["is_processing"] = True
    stop_event.clear()

    try:
        json_path = get_job_profile_path(video_id)
        with open(json_path, "r") as f: job_data = json.load(f)
        clips_map = {c["clip_id"]: c for c in job_data["clips"]}

        for i, clip_id in enumerate(clip_ids):
            if stop_event.is_set(): break
            processing_status["current_clip"] = clip_id
            clip_info = clips_map.get(clip_id)
            if not clip_info: continue

            print(f"\n>>> PROCESSING: {clip_id}", flush=True)

            if "TrimmedClips" in clip_info["path"]:
                 clean_name = Path(clip_info["path"]).name
                 current_source = INPUTS_DIR / video_id / "TrimmedClips" / clean_name
            else:
                 current_source = INPUTS_DIR / clip_info["path"]

            # FIX: Store the true original source for accurate comparison later
            original_source = current_source
            output_dir = OUTPUTS_DIR / video_id
            output_dir.mkdir(parents=True, exist_ok=True)

            actions = sorted(clip_info["actions"], key=lambda x: x["pass"])

            for action in actions:
                if stop_event.is_set(): break
                pass_num = action["pass"]
                processing_status["current_pass"] = pass_num
                
                print(f"   > Pass {pass_num} ({action['character']})", flush=True)

                mask_file = get_mask_path(video_id, clip_id, pass_num)
                mask_points = json.load(open(mask_file, "r")) if mask_file.exists() else None

                char_img = ASSETS_DIR / f"custom_{action['character']}.png"
                if not char_img.exists():
                    print(f"     [ERROR] Missing character image: {char_img}", flush=True)
                    continue

                job_prefix = f"DF_{video_id}_{clip_id}_pass{pass_num}"
                
                try:
                    real_fn = comfy_client.generate_clip(
                        source_video_path=current_source, character_image_path=char_img,
                        mask_path=None, output_filename=job_prefix, video_id=video_id, mask_points=mask_points
                    )
                    
                    if not real_fn:
                        print("     [ERROR] Generation failed.", flush=True)
                        break

                    temp_out = output_dir / f"{job_prefix}.mp4"
                    if move_comfy_output(real_fn, temp_out):
                        current_source = temp_out
                    else:
                        print("     [ERROR] Download failed.", flush=True)
                        break
                
                except Exception as e:
                    print(f"     [EXCEPTION] {e}", flush=True)
                    traceback.print_exc()
                    break

            # FIX: Only copy and mark as DONE if a pass successfully generated a new video
            final_dest = output_dir / f"{clip_id}.mp4"
            if current_source.exists() and current_source != original_source:
                 shutil.copy(current_source, final_dest)
                 print(f"   [DONE] Saved deepfake: {final_dest}", flush=True)
                 processing_status["last_completed"] = clip_id
            else:
                 print(f"   [SKIPPED] Generation failed or bypassed for: {clip_id}", flush=True)

    except Exception as e:
        print(f"[QUEUE ERROR] {e}", flush=True)
    finally:
        processing_status["is_processing"] = False
        processing_status["current_clip"] = None

# --- 8. ENDPOINTS ---

@app.get("/projects")
def list_projects(): return [d.name for d in INPUTS_DIR.iterdir() if d.is_dir() and (d / f"{d.name}.job.json").exists()]

@app.get("/project/{video_id}")
def get_project(video_id: str):
    json_path = get_job_profile_path(video_id)
    with open(json_path, "r") as f: data = json.load(f)
    out_dir = OUTPUTS_DIR / video_id
    for clip in data["clips"]: clip["status"] = "done" if (out_dir / f"{clip['clip_id']}.mp4").exists() else "pending"
    return data

@app.get("/frame/{video_id}/{clip_id}")
def get_frame(video_id: str, clip_id: str, frame: int = 0):
    clip_filename = f"{clip_id}.mp4"
    clip_file = INPUTS_DIR / video_id / "TrimmedClips" / clip_filename
    if not clip_file.exists(): raise HTTPException(status_code=404, detail="Source clip not found")
    temp_frame = OUTPUTS_DIR / video_id / "frames" / f"{clip_id}_f{frame}.jpg"
    temp_frame.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(clip_file), "-vf", f"select=eq(n\\,{frame})", "-vframes", "1", "-q:v", "2", str(temp_frame)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"url": f"/outputs/{video_id}/frames/{temp_frame.name}"}

@app.post("/mask/save/{video_id}/{clip_id}/{pass_num}")
async def save_mask(video_id: str, clip_id: str, pass_num: int, data: Dict):
    mask_path = get_mask_path(video_id, clip_id, pass_num)
    with open(mask_path, "w") as f: json.dump(data, f, indent=2)
    return {"status": "saved"}

@app.get("/mask/load/{video_id}/{clip_id}/{pass_num}")
def load_mask(video_id: str, clip_id: str, pass_num: int):
    mask_path = get_mask_path(video_id, clip_id, pass_num)
    if not mask_path.exists(): raise HTTPException(status_code=404, detail="No saved mask")
    return json.load(open(mask_path, "r"))

@app.post("/character/upload/{character_name}")
async def upload_character(character_name: str, file: UploadFile = File(...)):
    save_path = ASSETS_DIR / f"custom_{character_name}.png"
    with open(save_path, "wb") as buffer: shutil.copyfileobj(file.file, buffer)
    return {"status": "uploaded", "url": f"/assets/custom_{character_name}.png?t={int(time.time())}"}

@app.get("/characters/check")
def check_characters(): return {"char1": (ASSETS_DIR / "custom_char1.png").exists(), "char2": (ASSETS_DIR / "custom_char2.png").exists()}

@app.post("/queue/clip/{video_id}")
async def queue_single_clip(video_id: str, clip_data: Dict, token: str = Depends(get_api_key)):
    JOB_QUEUE.put((video_id, [clip_data["clip_id"]]))
    return {"status": "queued"}

@app.post("/queue/all/{video_id}")
async def queue_all_clips(video_id: str, token: str = Depends(get_api_key)):
    json_path = get_job_profile_path(video_id)
    with open(json_path, "r") as f: data = json.load(f)
    ids = [c["clip_id"] for c in data["clips"]]
    JOB_QUEUE.put((video_id, ids))
    return {"status": "queued", "count": len(ids)}

@app.post("/stop")
def stop_generation(token: str = Depends(get_api_key)):
    stop_event.set()
    while not JOB_QUEUE.empty():
        try: JOB_QUEUE.get_nowait(); JOB_QUEUE.task_done()
        except: pass
    return {"status": "stopped"}

@app.post("/reset/{video_id}")
def reset_project(video_id: str, token: str = Depends(get_api_key)):
    out_dir = OUTPUTS_DIR / video_id
    if out_dir.exists():
        for f in out_dir.glob("*.mp4"):
            try: f.unlink()
            except: pass
    return {"status": "reset"}

@app.post("/stitch/{video_id}")
def stitch_video(video_id: str):
    print(f"[STITCH] Starting for {video_id}", flush=True)
    out_dir = OUTPUTS_DIR / video_id
    json_path = get_job_profile_path(video_id)
    with open(json_path) as f: data = json.load(f)
    list_file = out_dir / "list.txt"
    final_video = out_dir / f"{video_id}_final.mp4"
    
    with open(list_file, "w") as f:
        for clip in data["clips"]:
            clip_file = out_dir / f"{clip['clip_id']}.mp4"
            if clip_file.exists(): f.write(f"file '{str(clip_file.resolve()).replace(os.sep, '/')}'\n")
            else:
                clean_name = Path(clip["path"]).name
                source = INPUTS_DIR / video_id / "TrimmedClips" / clean_name
                temp_conv = out_dir / f"temp_{clip['clip_id']}.mp4"
                if not temp_conv.exists() and source.exists():
                    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(data["fps"]), str(temp_conv)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if temp_conv.exists(): f.write(f"file '{str(temp_conv.resolve()).replace(os.sep, '/')}'\n")

    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(final_video)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"url": f"/outputs/{video_id}/{final_video.name}"}

@app.get("/status")
def get_status(): return processing_status

@app.get("/")
def health_check(): return {"status": "online", "mode": "Cloud" if IS_CLOUD else "Local"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)