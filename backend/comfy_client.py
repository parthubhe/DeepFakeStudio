#--- START OF FILE comfy_client.py ---

import websocket
import uuid
import json
import urllib.request
import urllib.parse
import requests
import os
import time
from pathlib import Path

SERVER_ADDRESS = "127.0.0.1:8188"
CLIENT_ID = str(uuid.uuid4())
BASE_DIR = Path(__file__).resolve().parent


def queue_prompt(prompt):
    print("=== SENDING PROMPT TO COMFY ===")

    p = {"prompt": prompt, "client_id": CLIENT_ID}
    data = json.dumps(p).encode("utf-8")

    req = urllib.request.Request(
        f"http://{SERVER_ADDRESS}/prompt",
        data=data,
        headers={"Content-Type": "application/json"}
    )

    response = urllib.request.urlopen(req)
    result = json.loads(response.read())

    print("Comfy /prompt response:", result)
    return result


def get_history(prompt_id):
    with urllib.request.urlopen(
        f"http://{SERVER_ADDRESS}/history/{prompt_id}"
    ) as response:
        return json.loads(response.read())


def upload_file(file_path, subfolder="", overwrite=True):
    print(f"[UPLOAD] {file_path}")

    try:
        with open(file_path, "rb") as f:
            files = {"image": f}
            data = {
                "subfolder": subfolder,
                "overwrite": str(overwrite).lower()
            }

            response = requests.post(
                f"http://{SERVER_ADDRESS}/upload/image",
                files=files,
                data=data
            )

        print("Upload response:", response.status_code, response.text)
        return response.json()

    except Exception as e:
        print("Upload failed:", e)
        return None


def connect_websocket():
    print("Connecting WS...")
    ws = websocket.WebSocket()
    ws.connect(f"ws://{SERVER_ADDRESS}/ws?clientId={CLIENT_ID}")
    return ws


def track_progress(ws, prompt_id):
    print("Tracking progress for:", prompt_id)

    while True:
        try:
            out = ws.recv()
            if isinstance(out, str):
                message = json.loads(out)

                if message["type"] == "executing":
                    data = message["data"]

                    if data["node"] is None and data["prompt_id"] == prompt_id:
                        print("Execution complete.")
                        return True
        except Exception as e:
            print("WS Error or Close:", e)
            break


def load_workflow_template():
    workflow_path = BASE_DIR / "workflow_template.json"
    print("Loading workflow from:", workflow_path)

    with open(workflow_path, "r", encoding="utf-8") as f:
        return json.load(f)


# =========================================================
# MAIN GENERATION FUNCTION (UPDATED)
# =========================================================
def generate_clip(
    source_video_path,
    character_image_path,
    mask_path,
    output_filename,
    video_id=None,
    seed=None,
    mask_points=None
):

    print("\n=================================")
    print("GENERATE_CLIP CALLED")
    print("Source:", source_video_path)
    print("Character:", character_image_path)
    print("Output:", output_filename)
    print("Video ID:", video_id)
    print("=================================")

    # -------------------------------------
    # 1️⃣ Upload Source Video
    # -------------------------------------
    vid_resp = upload_file(source_video_path)
    if not vid_resp:
        raise RuntimeError("Video upload failed")

    vid_name = vid_resp["name"]

    # -------------------------------------
    # 2️⃣ Upload Character Image
    # -------------------------------------
    img_resp = upload_file(character_image_path)
    if not img_resp:
        raise RuntimeError("Image upload failed")

    img_name = img_resp["name"]

    # -------------------------------------
    # 3️⃣ Load Workflow Template
    # -------------------------------------
    workflow = load_workflow_template()

    # -------------------------------------
    # 4️⃣ Hardcoded Resolution Fix
    # -------------------------------------
    if video_id in ["Video1", "Video3"]:
        width = 832
        height = 480
    elif video_id == "Video2":
        width = 480
        height = 832
    else:
        width = 832
        height = 480

    print(f"[RESOLUTION SET] Width={width}, Height={height}")

    workflow["83"]["inputs"]["value"] = width
    workflow["76"]["inputs"]["value"] = height

        # -------------------------------------
    # Inject PointsEditor mask coordinates
    # -------------------------------------

    if mask_points:
        print("\n================ MASK DEBUG ================")
        print("Incoming mask_points:", mask_points)
        print("Positive:", mask_points.get("positive"))
        print("Negative:", mask_points.get("negative"))
        print("===========================================\n")

        workflow["77"]["inputs"]["points_store"] = json.dumps(mask_points)
        workflow["77"]["inputs"]["coordinates"] = json.dumps(mask_points.get("positive", []))
        workflow["77"]["inputs"]["neg_coordinates"] = json.dumps(mask_points.get("negative", []))

        # VALIDATION
        # assert workflow["77"]["inputs"]["coordinates"] != "[]", "Mask coordinates not injected!"
    else:
        print("WARNING: No mask points provided to generate_clip")


    # -------------------------------------
    # 5️⃣ Inject Uploaded Video Name
    # -------------------------------------
    workflow["79"]["inputs"]["video"] = vid_name
    workflow["117"]["inputs"]["video"] = vid_name

    # -------------------------------------
    # 6️⃣ Inject Character Image
    # -------------------------------------
    workflow["78"]["inputs"]["image"] = img_name

    # -------------------------------------
    # 7️⃣ Output Filename
    # -------------------------------------
    workflow["114"]["inputs"]["filename_prefix"] = output_filename

    # -------------------------------------
    # 8️⃣ Seed Handling
    # -------------------------------------
    if seed:
        workflow["3"]["inputs"]["seed"] = seed
    else:
        workflow["3"]["inputs"]["seed"] = (
            int(time.time() * 1000) % 10000000000
        )

    # -------------------------------------
    # 9️⃣ Queue Prompt
    # -------------------------------------
    prompt_response = queue_prompt(workflow)
    prompt_id = prompt_response["prompt_id"]

    ws = connect_websocket()

    print("Processing prompt:", prompt_id)
    track_progress(ws, prompt_id)

    ws.close()

    print("Generation finished:", output_filename)

    return output_filename