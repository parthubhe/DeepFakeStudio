#--- START OF FILE main.py ---

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi import UploadFile, File
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Optional,Dict,List
import json
import shutil
import subprocess
import comfy_client
import time
import traceback
import asyncio
import threading

app = FastAPI()

# =========================================================
# PATH CONFIG
# =========================================================
BASE_DIR = Path(__file__).resolve().parent

INPUTS_DIR  = BASE_DIR.parent / "inputs" / "videos"
OUTPUTS_DIR = BASE_DIR.parent / "outputs"
ASSETS_DIR  = BASE_DIR.parent / "assets"

COMFY_OUTPUT_DIR = Path(
    r"C:\Users\parth\Documents\ComfyUI\output"
)

INPUTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

print("==== BACKEND STARTED ====")
print("INPUTS_DIR:", INPUTS_DIR)
print("OUTPUTS_DIR:", OUTPUTS_DIR)
print("ASSETS_DIR:", ASSETS_DIR)
print("COMFY_OUTPUT_DIR:", COMFY_OUTPUT_DIR)

# =========================================================
# CORS
# =========================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# STATIC FILE SERVING
# =========================================================
app.mount("/inputs", StaticFiles(directory=INPUTS_DIR), name="inputs")
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# =========================================================
# MODELS
# =========================================================
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

# =========================================================
# STATE
# =========================================================
processing_status = {
    "is_processing": False,
    "current_clip": None,
    "current_pass": 0,
    "total_clips": 0,
    "processed_clips": 0,
    "queue": [],
    "last_completed": None # New field for alerting frontend
}

stop_event = threading.Event()

# =========================================================
# HELPERS
# =========================================================
def get_job_profile_path(video_id: str) -> Path:
    path = INPUTS_DIR / video_id / f"{video_id}.job.json"
    print(f"[DEBUG] Loading job profile: {path}")
    if not path.exists():
        raise FileNotFoundError(f"Job profile not found for {video_id}")
    return path


def move_comfy_output(prefix: str, dest_path: Path) -> bool:
    print(f"[DEBUG] Looking for Comfy output: {prefix}")
    # Retry logic for file system lag
    for i in range(10):
        if stop_event.is_set(): return False
        time.sleep(1)
        candidates = list(COMFY_OUTPUT_DIR.glob(f"{prefix}_*.mp4"))
        if candidates:
            break
    
    if not candidates:
        print("[ERROR] No Comfy output found.")
        return False
    
    latest = max(candidates, key=lambda p: p.stat().st_ctime)
    print(f"[DEBUG] Found Comfy output: {latest}")
    
    # Wait for write to finish
    time.sleep(2)
    shutil.copy(latest, dest_path)
    return True

# =========================================================
# MASK STORAGE HELPERS
# =========================================================

def get_mask_path(video_id: str, clip_id: str, pass_num: int) -> Path:
    mask_dir = OUTPUTS_DIR / video_id / "masks"
    mask_dir.mkdir(parents=True, exist_ok=True)
    return mask_dir / f"{clip_id}_pass{pass_num}.json"


# =========================================================
# PROCESSING ENGINE
# =========================================================

async def run_queue_processor(video_id: str, clip_ids: List[str]):
    """
    Process a list of clip IDs sequentially.
    Handles Single Pass and Multi-Pass (BothChar) clips.
    Dependency Preserving: Pass 2 uses Pass 1 output.
    """
    global processing_status
    
    processing_status["is_processing"] = True
    processing_status["total_clips"] = len(clip_ids)
    processing_status["processed_clips"] = 0
    processing_status["queue"] = clip_ids
    stop_event.clear()

    print(f"[QUEUE STARTED] Processing {len(clip_ids)} clips for {video_id}")

    try:
        # Load Job Data
        json_path = get_job_profile_path(video_id)
        with open(json_path, "r") as f:
            job_data = json.load(f)
        
        clips_map = {c["clip_id"]: c for c in job_data["clips"]}

        for i, clip_id in enumerate(clip_ids):
            if stop_event.is_set():
                print("[STOP] Processing halted by user.")
                break

            processing_status["current_clip"] = clip_id
            processing_status["processed_clips"] = i
            
            clip_info = clips_map.get(clip_id)
            if not clip_info:
                print(f"[ERROR] Clip {clip_id} not found in profile.")
                continue

            print(f"\n>>> PROCESSING CLIP: {clip_id} ({clip_info['type']})")

            # Determine video source
            video_root = INPUTS_DIR / video_id
            relative_path = Path(clip_info["path"])
            parts = relative_path.parts
            if "videos" in parts:
                idx = parts.index("videos")
                cleaned = Path(*parts[idx + 2:])
            else:
                cleaned = relative_path
            
            original_source = video_root / cleaned
            
            if not original_source.exists():
                print(f"[ERROR] Source file missing: {original_source}")
                continue

            current_source = original_source
            output_dir = OUTPUTS_DIR / video_id
            output_dir.mkdir(parents=True, exist_ok=True)

            # Iterate through actions (Passes)
            # Sort actions by pass number to ensure Pass 1 runs before Pass 2
            actions = sorted(clip_info["actions"], key=lambda x: x["pass"])

            for action in actions:
                if stop_event.is_set(): break
                
                pass_num = action["pass"]
                processing_status["current_pass"] = pass_num
                
                print(f"   > Pass {pass_num} (Char: {action['character']})")

                # 1. Load Mask
                mask_path_file = get_mask_path(video_id, clip_id, pass_num)
                
                mask_points = None
                if mask_path_file.exists():
                    with open(mask_path_file, "r") as mf:
                        mask_points = json.load(mf)
                    print(f"     [MASK] Loaded from {mask_path_file}")
                else:
                    print(f"     [WARNING] No mask file found for {clip_id} Pass {pass_num}. Proceeding without mask (Auto/None).")

                # 2. Determine Character Image
                custom_path = ASSETS_DIR / f"custom_{action['character']}.png"
                default_path = ASSETS_DIR / f"{action['character']}.png"
                ref_img = custom_path if custom_path.exists() else default_path
                
                if not ref_img.exists():
                    print(f"     [ERROR] Character image missing: {ref_img}")
                    continue

                # 3. Construct Output Filename
                job_prefix = f"DF_{video_id}_{clip_id}_pass{pass_num}"
                
                # 4. Run Comfy
                try:
                    comfy_client.generate_clip(
                        source_video_path=current_source,
                        character_image_path=ref_img,
                        mask_path=None, 
                        output_filename=job_prefix,
                        video_id=video_id,
                        mask_points=mask_points
                    )
                    
                    # 5. Move Output
                    temp_out = output_dir / f"{job_prefix}.mp4"
                    if move_comfy_output(job_prefix, temp_out):
                        # Update current_source for the NEXT pass
                        current_source = temp_out
                    else:
                        print(f"     [ERROR] ComfyUI did not produce output for pass {pass_num}")
                        break # Stop passes for this clip
                
                except Exception as e:
                    print(f"     [EXCEPTION] {e}")
                    traceback.print_exc()
                    break

            # End of Clip Actions
            # Copy final result to pure clip_id.mp4 for frontend easy access
            final_dest = output_dir / f"{clip_id}.mp4"
            if current_source.exists() and current_source != original_source:
                 shutil.copy(current_source, final_dest)
                 print(f"   [DONE] Clip finished. Saved to {final_dest}")
                 # Update status for frontend alert
                 processing_status["last_completed"] = clip_id

        # End of Loop
        if not stop_event.is_set():
             # Auto Stitch
             print("\n[AUTO STITCH] Queue finished, stitching...")
             stitch_video(video_id)

    except Exception as e:
        print("[QUEUE ERROR]", e)
        traceback.print_exc()
    finally:
        processing_status["is_processing"] = False
        processing_status["current_clip"] = None
        processing_status["queue"] = []
        print("[QUEUE] Processor stopped.")

# =========================================================
# API ENDPOINTS
# =========================================================

@app.get("/projects")
def list_projects():
    projects = [
        d.name
        for d in INPUTS_DIR.iterdir()
        if d.is_dir() and (d / f"{d.name}.job.json").exists()
    ]
    return projects

@app.get("/project/{video_id}")
def get_project(video_id: str):
    try:
        json_path = get_job_profile_path(video_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job profile not found")

    with open(json_path, "r") as f:
        data = json.load(f)

    out_dir = OUTPUTS_DIR / video_id
    out_dir.mkdir(parents=True, exist_ok=True)

    for clip in data["clips"]:
        # Check if final output exists
        if (out_dir / f"{clip['clip_id']}.mp4").exists():
            clip["status"] = "done"
        else:
            clip["status"] = "pending"

    return data

@app.get("/frame/{video_id}/{clip_id}")
def get_frame(video_id: str, clip_id: str, frame: int = 0):
    clip_file = INPUTS_DIR / video_id / "TrimmedClips" / f"{clip_id}.mp4"
    if not clip_file.exists():
        raise HTTPException(status_code=404, detail="Clip file missing")

    temp_frame = OUTPUTS_DIR / video_id / "frames" / f"{clip_id}_f{frame}.jpg"
    temp_frame.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run([
        "ffmpeg", "-y", "-i", str(clip_file),
        "-vf", f"select=eq(n\\,{frame})",
        "-vframes", "1", "-q:v", "2",
        str(temp_frame)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not temp_frame.exists():
        raise HTTPException(status_code=500, detail="Frame extraction failed")

    return {"url": f"/outputs/{video_id}/frames/{temp_frame.name}"}

@app.post("/mask/save/{video_id}/{clip_id}/{pass_num}")
async def save_mask(video_id: str, clip_id: str, pass_num: int, data: Dict):
    print(f"\n[MASK SAVE] {video_id} {clip_id} Pass:{pass_num}")
    mask_path = get_mask_path(video_id, clip_id, pass_num)
    with open(mask_path, "w") as f:
        json.dump(data, f, indent=2)
    return {"status": "saved"}

@app.get("/mask/load/{video_id}/{clip_id}/{pass_num}")
def load_mask(video_id: str, clip_id: str, pass_num: int):
    mask_path = get_mask_path(video_id, clip_id, pass_num)
    if not mask_path.exists():
        raise HTTPException(status_code=404, detail="No saved mask found")
    with open(mask_path, "r") as f:
        data = json.load(f)
    return data

@app.post("/character/upload/{character_name}")
async def upload_character(character_name: str, file: UploadFile = File(...)):
    print(f"\n[CHARACTER UPLOAD] {character_name}")
    save_path = ASSETS_DIR / f"custom_{character_name}.png"
    
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"status": "uploaded", "url": f"/assets/custom_{character_name}.png?t={int(time.time())}"}

@app.get("/characters/check")
def check_characters():
    """Check which custom characters are uploaded"""
    c1 = (ASSETS_DIR / "custom_char1.png").exists()
    c2 = (ASSETS_DIR / "custom_char2.png").exists()
    return {"char1": c1, "char2": c2}

@app.post("/queue/clip/{video_id}")
async def queue_single_clip(
    video_id: str,
    clip_data: Dict,
    background_tasks: BackgroundTasks
):
    """
    Queue a single clip.
    Frontend should have already saved the mask(s).
    """
    clip_id = clip_data["clip_id"]
    print(f"[QUEUE SINGLE] {clip_id}")
    
    # We use the common processor but with a list of one
    background_tasks.add_task(run_queue_processor, video_id, [clip_id])
    return {"status": "queued"}

@app.post("/queue/all/{video_id}")
async def queue_all_clips(video_id: str, background_tasks: BackgroundTasks):
    """
    Queue ALL clips in the project.
    Validates that masks exist for all passes before starting.
    """
    json_path = get_job_profile_path(video_id)
    with open(json_path, "r") as f:
        data = json.load(f)

    missing_masks = []
    clips_to_queue = []

    for clip in data["clips"]:
        clips_to_queue.append(clip["clip_id"])
        
        # Check masks for each pass
        for action in clip["actions"]:
            pass_num = action["pass"]
            mask_path = get_mask_path(video_id, clip["clip_id"], pass_num)
            
            # Note: NoChar type clips usually have no actions or pass=0, skipping logic for them
            if clip["type"] == "NoChar":
                continue
                
            if not mask_path.exists():
                missing_masks.append(f"{clip['clip_id']} (Pass {pass_num})")

    if missing_masks:
        return {
            "status": "error",
            "message": "Missing masks for the following clips. Please generate masks first.",
            "missing": missing_masks
        }

    print(f"[QUEUE ALL] Queuing {len(clips_to_queue)} clips")
    background_tasks.add_task(run_queue_processor, video_id, clips_to_queue)
    
    return {"status": "queued", "count": len(clips_to_queue)}

@app.post("/stop")
def stop_generation():
    print("[STOP REQUESTED]")
    stop_event.set()
    return {"status": "stopping"}

@app.post("/reset/{video_id}")
def reset_project(video_id: str):
    print(f"[RESET] Resetting project {video_id}...")
    out_dir = OUTPUTS_DIR / video_id
    if out_dir.exists():
        # Delete generated .mp4 files (DF_* and final clip names)
        # We preserve masks (.json)
        count = 0
        for file in out_dir.glob("*.mp4"):
            try:
                file.unlink()
                count += 1
            except Exception as e:
                print(f"[RESET ERROR] Failed to delete {file}: {e}")
        print(f"[RESET] Deleted {count} files.")
    
    return {"status": "reset"}

@app.post("/stitch/{video_id}")
def stitch_video(video_id: str):
    print("[DEBUG] Stitching:", video_id)
    out_dir = OUTPUTS_DIR / video_id
    if not out_dir.exists():
         raise HTTPException(status_code=404, detail="No outputs found")

    json_path = get_job_profile_path(video_id)
    with open(json_path) as f:
        data = json.load(f)
        
    target_fps = data.get("fps", 24) # Default to 24 if missing

    list_file = out_dir / "list.txt"
    final_video = out_dir / f"{video_id}_final.mp4"

    # Ensure we use absolute paths for ffmpeg list to avoid confusion
    with open(list_file, "w") as f:
        for clip in data["clips"]:
            # 1. Target output file
            clip_file = out_dir / f"{clip['clip_id']}.mp4"
            
            # 2. Check if generated file exists
            if not clip_file.exists():
                 # 3. If not, try to find original source (Fallback)
                 print(f"[STITCH] Clip {clip['clip_id']} missing output, looking for source...")
                 
                 video_root = INPUTS_DIR / video_id
                 relative = Path(clip["path"])
                 parts = relative.parts
                 
                 # Resolve source path logic
                 if "videos" in parts:
                    idx = parts.index("videos")
                    cleaned = Path(*parts[idx + 2:])
                    source = video_root / cleaned
                 else:
                    source = video_root / relative

                 print(f"[STITCH] Checking source: {source}")

                 if source.exists():
                     # ----------------------------------------------------
                     # FIX: Re-encode Source Clips to match target FPS
                     # ----------------------------------------------------
                     print(f"[STITCH] Converting source to {target_fps}fps at {clip_file}...")
                     try:
                        subprocess.run([
                            "ffmpeg", "-y", "-i", str(source),
                            "-r", str(target_fps),
                            "-c:v", "libx264",
                            "-pix_fmt", "yuv420p",
                            str(clip_file)
                        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                     except Exception as e:
                        print(f"[STITCH ERROR] Failed to convert source for {clip['clip_id']}: {e}")
                 else:
                     print(f"[STITCH ERROR] Source also missing for {clip['clip_id']}, skipping frame!")
                     continue

            # 4. Write to list file
            if clip_file.exists():
                # Escape path for ffmpeg
                safe_path = str(clip_file.resolve()).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

    print("[DEBUG] Running ffmpeg concat...")
    
    # Run ffmpeg concat with re-encode to ensure everything aligns
    try:
        result = subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-vsync", "2", # Drop/Dupe frames to maintain sync
            str(final_video)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if result.returncode != 0:
            print("[FFMPEG ERROR]", result.stderr.decode("utf-8"))
            raise RuntimeError("ffmpeg concatenation failed")
            
        print("[SUCCESS] Final stitched:", final_video)
        
    except Exception as e:
         print(f"[STITCH FATAL] {e}")
         raise HTTPException(status_code=500, detail="Stitching process failed")

    return {"url": f"/outputs/{video_id}/{video_id}_final.mp4"}

@app.get("/status")
def get_status():
    return processing_status

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)