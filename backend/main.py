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

load_dotenv()
app = FastAPI()

# --- 1. CONFIGURATION & PATHS ---
# If on Render, use /tmp for writeable storage. If Local, use project dir.
IS_CLOUD = os.getenv("RENDER") is not None
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent

# INPUTS must act as Read-Only from Repo on Cloud
INPUTS_DIR = REPO_ROOT / "inputs" / "videos"

# OUTPUTS/ASSETS must be writable
if IS_CLOUD:
    STORAGE_ROOT = Path("/tmp")
    OUTPUTS_DIR = STORAGE_ROOT / "outputs"
    ASSETS_DIR = STORAGE_ROOT / "assets"
else:
    OUTPUTS_DIR = REPO_ROOT / "outputs"
    ASSETS_DIR = REPO_ROOT / "assets"

for d in [OUTPUTS_DIR, ASSETS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

print(f"==== BACKEND STARTED ({'CLOUD' if IS_CLOUD else 'LOCAL'}) ====")
print("INPUTS:", INPUTS_DIR)
print("OUTPUTS:", OUTPUTS_DIR)

# --- 2. SECURITY ---
API_KEY_NAME = "X-Access-Token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def get_api_key(api_key_header: str = Security(api_key_header)):
    # Localhost development: Allow no key
    if not IS_CLOUD: 
        return "dev-mode"
        
    allowed_keys = os.getenv("ALLOWED_KEYS", "").split(",")
    if api_key_header in allowed_keys:
        return api_key_header
    raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="Invalid Access Token")

# --- 3. CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 4. STATIC FILES ---
# Serve inputs from Repo (Read Only)
app.mount("/inputs", StaticFiles(directory=INPUTS_DIR), name="inputs")
# Serve outputs/assets from Writable Storage
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# --- 5. MODELS ---
class ClipAction(BaseModel):
    pass_num: int = Field(alias="pass")
    character: str
    mask: str = "AUTO"
    class Config:
        allow_population_by_field_name = True

class ClipInfo(BaseModel):
    clip_id: str
    path: str
    start: int
    end: int
    type: str
    actions: List[ClipAction]
    status: str = "pending"
    mask_points: Optional[Dict] = None

class JobProfile(BaseModel):
    video_id: str
    fps: int
    clips: List[ClipInfo]

# --- 6. JOB QUEUE ---
JOB_QUEUE = queue.Queue()
stop_event = threading.Event()
processing_status = {
    "is_processing": False,
    "current_clip": None,
    "current_pass": 0,
    "queue_size": 0,
    "last_completed": None
}

def worker_loop():
    print("[WORKER] Thread started...")
    while True:
        task = JOB_QUEUE.get()
        if task is None: break
        
        video_id, clip_ids = task
        try:
            processing_status["queue_size"] = JOB_QUEUE.qsize()
            run_queue_processor(video_id, clip_ids)
        except Exception as e:
            print(f"[WORKER ERROR] {e}")
            traceback.print_exc()
        finally:
            JOB_QUEUE.task_done()
            processing_status["queue_size"] = JOB_QUEUE.qsize()

@app.on_event("startup")
def startup_event():
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()

# --- 7. HELPERS ---
def get_job_profile_path(video_id: str) -> Path:
    # Look in repo inputs
    path = INPUTS_DIR / video_id / f"{video_id}.job.json"
    if not path.exists():
        # Fallback for flat structure
        path = INPUTS_DIR / f"{video_id}.job.json"
    if not path.exists():
         raise FileNotFoundError(f"Job profile not found for {video_id}")
    return path

def get_mask_path(video_id: str, clip_id: str, pass_num: int) -> Path:
    mask_dir = OUTPUTS_DIR / video_id / "masks"
    mask_dir.mkdir(parents=True, exist_ok=True)
    return mask_dir / f"{clip_id}_pass{pass_num}.json"

def move_comfy_output(remote_filename: str, dest_path: Path) -> bool:
    print(f"[DEBUG] Downloading: {remote_filename}")
    from comfy_client import SERVER_ADDRESS, HTTP_PROTO, get_auth_header
    
    url = f"{HTTP_PROTO}://{SERVER_ADDRESS}/view?filename={remote_filename}&subfolder=&type=output"
    
    for attempt in range(5):
        if stop_event.is_set(): return False
        try:
            r = requests.get(url, headers=get_auth_header(), stream=True)
            if r.status_code == 200:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                print(f"[SUCCESS] Saved to {dest_path}")
                return True
            elif r.status_code == 404:
                print(f"   Waiting for file... {attempt+1}/5")
        except Exception as e:
            print(f"   Download error: {e}")
        time.sleep(2)
    return False

# --- 8. PROCESSOR ---
def run_queue_processor(video_id: str, clip_ids: List[str]):
    global processing_status
    processing_status["is_processing"] = True
    stop_event.clear()

    try:
        json_path = get_job_profile_path(video_id)
        with open(json_path, "r") as f:
            job_data = json.load(f)
        
        clips_map = {c["clip_id"]: c for c in job_data["clips"]}

        for i, clip_id in enumerate(clip_ids):
            if stop_event.is_set(): break
            processing_status["current_clip"] = clip_id
            
            clip_info = clips_map.get(clip_id)
            if not clip_info: continue

            print(f"\n>>> PROCESSING: {clip_id}")

            # Path logic to handle "TrimmedClips" nesting
            if "TrimmedClips" in clip_info["path"]:
                 # Standard structure: Video1/TrimmedClips/clip.mp4
                 clean_name = Path(clip_info["path"]).name
                 current_source = INPUTS_DIR / video_id / "TrimmedClips" / clean_name
            else:
                 current_source = INPUTS_DIR / clip_info["path"]

            output_dir = OUTPUTS_DIR / video_id
            output_dir.mkdir(parents=True, exist_ok=True)

            # Sort passes
            actions = sorted(clip_info["actions"], key=lambda x: x["pass"])

            for action in actions:
                if stop_event.is_set(): break
                pass_num = action["pass"]
                processing_status["current_pass"] = pass_num
                
                print(f"   > Pass {pass_num} ({action['character']})")

                # Load Mask
                mask_file = get_mask_path(video_id, clip_id, pass_num)
                mask_points = json.load(open(mask_file, "r")) if mask_file.exists() else None

                # Load Character Image
                char_img = ASSETS_DIR / f"custom_{action['character']}.png"
                if not char_img.exists():
                    print(f"     [ERROR] Missing character image: {char_img}")
                    continue

                job_prefix = f"DF_{video_id}_{clip_id}_pass{pass_num}"
                
                try:
                    real_fn = comfy_client.generate_clip(
                        source_video_path=current_source,
                        character_image_path=char_img,
                        mask_path=None, 
                        output_filename=job_prefix,
                        video_id=video_id,
                        mask_points=mask_points
                    )
                    
                    if not real_fn:
                        print("     [ERROR] Generation failed.")
                        break

                    temp_out = output_dir / f"{job_prefix}.mp4"
                    if move_comfy_output(real_fn, temp_out):
                        current_source = temp_out
                    else:
                        print("     [ERROR] Download failed.")
                        break
                
                except Exception as e:
                    print(f"     [EXCEPTION] {e}")
                    traceback.print_exc()
                    break

            # Final Save
            final_dest = output_dir / f"{clip_id}.mp4"
            if current_source.exists() and current_source != INPUTS_DIR / clip_info["path"]:
                 shutil.copy(current_source, final_dest)
                 print(f"   [DONE] Saved: {final_dest}")
                 processing_status["last_completed"] = clip_id

        if not stop_event.is_set():
             stitch_video(video_id)

    except Exception as e:
        print("[QUEUE ERROR]", e)
    finally:
        processing_status["is_processing"] = False
        processing_status["current_clip"] = None

# --- 9. ENDPOINTS ---

@app.get("/projects")
def list_projects():
    # Only list directories that contain a job.json
    return [d.name for d in INPUTS_DIR.iterdir() if d.is_dir() and (d / f"{d.name}.job.json").exists()]

@app.get("/project/{video_id}")
def get_project(video_id: str):
    json_path = get_job_profile_path(video_id)
    with open(json_path, "r") as f:
        data = json.load(f)

    # Check status against OUTPUTS_DIR
    out_dir = OUTPUTS_DIR / video_id
    for clip in data["clips"]:
        if (out_dir / f"{clip['clip_id']}.mp4").exists():
            clip["status"] = "done"
        else:
            clip["status"] = "pending"
    return data

@app.get("/frame/{video_id}/{clip_id}")
def get_frame(video_id: str, clip_id: str, frame: int = 0):
    # Extract filename from the path in JSON
    # Assuming standard structure
    clip_filename = f"{clip_id}.mp4"
    clip_file = INPUTS_DIR / video_id / "TrimmedClips" / clip_filename
    
    if not clip_file.exists():
         raise HTTPException(status_code=404, detail=f"Source clip not found: {clip_file}")

    temp_frame = OUTPUTS_DIR / video_id / "frames" / f"{clip_id}_f{frame}.jpg"
    temp_frame.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run([
        "ffmpeg", "-y", "-i", str(clip_file),
        "-vf", f"select=eq(n\\,{frame})",
        "-vframes", "1", "-q:v", "2",
        str(temp_frame)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return {"url": f"/outputs/{video_id}/frames/{temp_frame.name}"}

@app.post("/mask/save/{video_id}/{clip_id}/{pass_num}")
async def save_mask(video_id: str, clip_id: str, pass_num: int, data: Dict):
    mask_path = get_mask_path(video_id, clip_id, pass_num)
    with open(mask_path, "w") as f:
        json.dump(data, f, indent=2)
    return {"status": "saved"}

@app.get("/mask/load/{video_id}/{clip_id}/{pass_num}")
def load_mask(video_id: str, clip_id: str, pass_num: int):
    mask_path = get_mask_path(video_id, clip_id, pass_num)
    if not mask_path.exists():
        raise HTTPException(status_code=404, detail="No saved mask")
    return json.load(open(mask_path, "r"))

@app.post("/character/upload/{character_name}")
async def upload_character(character_name: str, file: UploadFile = File(...)):
    save_path = ASSETS_DIR / f"custom_{character_name}.png"
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "uploaded", "url": f"/assets/custom_{character_name}.png?t={int(time.time())}"}

@app.get("/characters/check")
def check_characters():
    return {
        "char1": (ASSETS_DIR / "custom_char1.png").exists(),
        "char2": (ASSETS_DIR / "custom_char2.png").exists()
    }

# PROTECTED ROUTES
@app.post("/queue/clip/{video_id}")
async def queue_single_clip(video_id: str, clip_data: Dict, token: str = Depends(get_api_key)):
    JOB_QUEUE.put((video_id, [clip_data["clip_id"]]))
    return {"status": "queued"}

@app.post("/queue/all/{video_id}")
async def queue_all_clips(video_id: str, token: str = Depends(get_api_key)):
    json_path = get_job_profile_path(video_id)
    with open(json_path, "r") as f:
        data = json.load(f)
    
    ids = [c["clip_id"] for c in data["clips"]]
    JOB_QUEUE.put((video_id, ids))
    return {"status": "queued", "count": len(ids)}

@app.post("/stop")
def stop_generation(token: str = Depends(get_api_key)):
    stop_event.set()
    while not JOB_QUEUE.empty():
        try:
            JOB_QUEUE.get_nowait()
            JOB_QUEUE.task_done()
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
    print(f"[STITCH] Starting for {video_id}")
    out_dir = OUTPUTS_DIR / video_id
    json_path = get_job_profile_path(video_id)
    with open(json_path) as f: data = json.load(f)
    
    list_file = out_dir / "list.txt"
    final_video = out_dir / f"{video_id}_final.mp4"
    
    with open(list_file, "w") as f:
        for clip in data["clips"]:
            # Check output first
            clip_file = out_dir / f"{clip['clip_id']}.mp4"
            if clip_file.exists():
                f.write(f"file '{str(clip_file.resolve()).replace(os.sep, '/')}'\n")
            else:
                # Fallback to input (might need conversion to match fps/encoding)
                # For simplicity in this version, we assume input exists
                clean_name = Path(clip["path"]).name
                source = INPUTS_DIR / video_id / "TrimmedClips" / clean_name
                # Need to convert source to temp file to match params or ffmpeg concat fails
                temp_conv = out_dir / f"temp_{clip['clip_id']}.mp4"
                if not temp_conv.exists() and source.exists():
                    subprocess.run([
                        "ffmpeg", "-y", "-i", str(source),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(data["fps"]),
                        str(temp_conv)
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                if temp_conv.exists():
                    f.write(f"file '{str(temp_conv.resolve()).replace(os.sep, '/')}'\n")

    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(final_video)
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    return {"url": f"/outputs/{video_id}/{final_video.name}"}

@app.get("/status")
def get_status():
    return processing_status

@app.get("/")
def health_check():
    return {"status": "online", "mode": "Cloud" if IS_CLOUD else "Local"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)