import websocket
import uuid
import json
import urllib.request
import urllib.parse
import requests
import os
import time
from pathlib import Path
import base64
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from dotenv import load_dotenv

load_dotenv()

# =================================================================================
# CONFIGURATION 
# =================================================================================

# RunPod HTTP Proxy Address (NO https://, NO trailing /)
SERVER_ADDRESS = os.getenv("COMFY_HOST", "194.68.245.1:22025") 
USE_SECURE = False
COMFY_AUTH = None 

# =================================================================================

BASE_DIR = Path(__file__).resolve().parent

HTTP_PROTO = "https" if USE_SECURE else "http"
WS_PROTO = "wss" if USE_SECURE else "ws"

def get_auth_header():
    if COMFY_AUTH:
        encoded = base64.b64encode(COMFY_AUTH.encode('utf-8')).decode('utf-8')
        return {"Authorization": f"Basic {encoded}"}
    return {}

def create_retry_session():
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504, 104, 10054],
        allowed_methods=["POST", "GET"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def queue_prompt(prompt, client_id):
    print(f"=== SENDING PROMPT TO {SERVER_ADDRESS} ===")
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode("utf-8")
    
    headers = {"Content-Type": "application/json"}
    headers.update(get_auth_header())
    headers["Connection"] = "close"

    url = f"{HTTP_PROTO}://{SERVER_ADDRESS}/prompt"
    
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read())
                print("✅ ComfyUI accepted prompt. ID:", result.get("prompt_id"))
                return result
        except Exception as e:
            print(f"⚠️ Queue attempt {attempt+1} failed: {e}")
            time.sleep(2)
            
    raise RuntimeError("Failed to queue prompt after 3 attempts.")

def upload_file(file_path, subfolder="", overwrite=True):
    print(f"[UPLOAD] Sending {file_path}...")
    
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return None

    session = create_retry_session()
    
    try:
        with open(file_path, "rb") as f:
            ext = Path(file_path).suffix.lower()
            if ext in ['.mp4', '.mov', '.webm']:
                content_type = f"video/{ext[1:]}"
            else:
                content_type = f"image/{ext[1:]}"
            
            files = {"image": (Path(file_path).name, f, content_type)}
            data = {"subfolder": subfolder, "overwrite": str(overwrite).lower()}
            
            headers = get_auth_header()
            headers["Connection"] = "close"

            response = session.post(
                f"{HTTP_PROTO}://{SERVER_ADDRESS}/upload/image",
                files=files,
                data=data,
                headers=headers,
                timeout=600 
            )

        print(f"✅ Upload response: {response.status_code}")
        if response.status_code != 200:
            print(f"   -> Failed: {response.text}")
            return None
            
        return response.json()
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return None
    finally:
        session.close()

def connect_websocket(client_id):
    print(f"🔌 Connecting to WebSocket {WS_PROTO}://{SERVER_ADDRESS}...")
    ws_url = f"{WS_PROTO}://{SERVER_ADDRESS}/ws?clientId={client_id}"
    
    ws = websocket.WebSocket()
    try:
        ws.connect(ws_url, timeout=10)
        print("✅ WebSocket connected")
        return ws
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        return None 

def track_progress(ws, prompt_id):
    if not ws: return False
    print(f"⏳ Tracking progress for {prompt_id}...")
    
    while True:
        try:
            out = ws.recv()
            if isinstance(out, str):
                message = json.loads(out)
                msg_type = message.get("type")
                
                if msg_type == "executing":
                    data = message["data"]
                    if data["node"] is None and data["prompt_id"] == prompt_id:
                        print("✅ Execution complete (WebSocket confirmed).")
                        return True
                    elif data["node"]:
                        print(f"   -> Executing Node: {data['node']}")
        except Exception as e:
            print(f"❌ WS Disconnected (Timeout/Network): {e}")
            break
            
    return False

def get_history(prompt_id):
    url = f"{HTTP_PROTO}://{SERVER_ADDRESS}/history/{prompt_id}"
    req = urllib.request.Request(url, headers=get_auth_header())
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())

def wait_for_completion(prompt_id):
    print(f"🔍 Polling history for confirmation of {prompt_id}...")
    start_time = time.time()
    
    while True:
        if time.time() - start_time > 1500: 
            raise RuntimeError("Timed out waiting for ComfyUI generation.")

        try:
            history = get_history(prompt_id)
            if prompt_id in history:
                outputs = history[prompt_id].get('outputs', {})
                if outputs:
                    print("✅ Job found in history with outputs!")
                    return history
            
        except Exception as e:
            print(f"   (polling error: {e}) - Retrying...")
        
        time.sleep(5) 

def load_workflow_template():
    workflow_path = BASE_DIR / "workflow_template.json"
    with open(workflow_path, "r", encoding="utf-8") as f:
        return json.load(f)

def generate_clip(source_video_path, character_image_path, mask_path, output_filename, video_id=None, seed=None, mask_points=None):
    
    # 1. Unique Client ID for this specific thread
    current_client_id = str(uuid.uuid4())

    vid_resp = upload_file(source_video_path)
    if not vid_resp: raise RuntimeError("Video upload failed")
    vid_name = vid_resp["name"]

    img_resp = upload_file(character_image_path)
    if not img_resp: raise RuntimeError("Image upload failed")
    img_name = img_resp["name"]

    workflow = load_workflow_template()
    
    if video_id in ["Video1", "Video3"]:
        width, height = 832, 480
    elif video_id == "Video2":
        width, height = 480, 832
    else:
        width, height = 832, 480
        
    workflow["83"]["inputs"]["value"] = width
    workflow["76"]["inputs"]["value"] = height

    if mask_points:
        workflow["77"]["inputs"]["points_store"] = json.dumps(mask_points)
        workflow["77"]["inputs"]["coordinates"] = json.dumps(mask_points.get("positive", []))
        workflow["77"]["inputs"]["neg_coordinates"] = json.dumps(mask_points.get("negative", []))

    workflow["79"]["inputs"]["video"] = vid_name
    if "119" in workflow: workflow["119"]["inputs"]["video"] = vid_name
    elif "117" in workflow: workflow["117"]["inputs"]["video"] = vid_name
        
    workflow["78"]["inputs"]["image"] = img_name
    workflow["114"]["inputs"]["filename_prefix"] = output_filename
    
    if seed: workflow["3"]["inputs"]["seed"] = seed
    else: workflow["3"]["inputs"]["seed"] = int(time.time() * 1000) % 10000000000

    prompt_response = queue_prompt(workflow, current_client_id)
    prompt_id = prompt_response["prompt_id"]
    
    ws = connect_websocket(current_client_id)
    if ws:
        track_progress(ws, prompt_id)
        ws.close()
    
    history = wait_for_completion(prompt_id)
    outputs = history.get(prompt_id, {}).get('outputs', {})
    
    print(f"[DEBUG] Validating outputs against prefix: '{output_filename}'")
    
    for node_id, node_output in outputs.items():
        if 'videos' in node_output:
            for item in node_output['videos']:
                if item['filename'].startswith(output_filename):
                    print(f"✅ Found output: {item['filename']}")
                    return item['filename']
        if 'gifs' in node_output:
            for item in node_output['gifs']:
                if item['filename'].startswith(output_filename):
                    print(f"✅ Found output: {item['filename']}")
                    return item['filename']

    if "114" in outputs and 'videos' in outputs["114"]:
         return outputs["114"]['videos'][0]['filename']

    print("❌ CRITICAL: No matching video file found.")
    return None