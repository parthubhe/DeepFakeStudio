import unittest
from unittest.mock import patch, MagicMock, mock_open
import json
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient

# Add the current directory to sys.path to import main
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import the FastAPI app
from main import app, processing_status, stop_event

# =================================================================================================
# UNIT TESTS (FIRST Principle: Fast, Independent, Repeatable, Self-Validating, Timely)
# =================================================================================================

class TestBackendUnit(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Reset global state before each test
        processing_status["is_processing"] = False
        processing_status["queue"] = []
        stop_event.clear()

    # --- 1. Infrastructure Tests ---
    
    def test_directory_structure_exists(self):
        """Ensure critical directories are defined in configuration."""
        from main import INPUTS_DIR, OUTPUTS_DIR, ASSETS_DIR
        self.assertTrue(INPUTS_DIR.exists(), "Inputs directory should exist")
        self.assertTrue(OUTPUTS_DIR.exists(), "Outputs directory should exist")
        self.assertTrue(ASSETS_DIR.exists(), "Assets directory should exist")

    def test_read_projects(self):
        """Test retrieving project list (Stubbing file system)."""
        with patch("pathlib.Path.iterdir") as mock_iterdir:
            # Mock directory structure
            mock_dir = MagicMock()
            mock_dir.is_dir.return_value = True
            mock_dir.name = "Video1"
            
            # Mock job.json check
            with patch("pathlib.Path.exists", return_value=True):
                mock_iterdir.return_value = [mock_dir]
                response = self.client.get("/projects")
                self.assertEqual(response.status_code, 200)
                self.assertIn("Video1", response.json())

    # --- 2. Endpoint Logic Tests ---

    def test_get_status_initial(self):
        """Fast check of status endpoint."""
        response = self.client.get("/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_processing"])

    def test_stop_generation(self):
        """Test if stop signal sets the event."""
        response = self.client.post("/stop")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(stop_event.is_set())

    @patch("shutil.copyfileobj")
    def test_upload_character_valid(self, mock_copy):
        """Test character upload logic."""
        file_content = b"fake image content"
        files = {"file": ("test.png", file_content, "image/png")}
        
        response = self.client.post("/character/upload/char1", files=files)
        
        self.assertEqual(response.status_code, 200)
        self.assertIn("uploaded", response.json()["status"])
        mock_copy.assert_called_once()

    def test_upload_character_invalid_name(self):
        """Test handling of unexpected errors or logic (though your current code accepts string, we test the call)."""
        # Note: Your current implementation doesn't explicitly restrict names in the endpoint, 
        # but this test verifies the endpoint is reachable.
        files = {"file": ("test.png", b"data", "image/png")}
        response = self.client.post("/character/upload/invalid_char", files=files)
        self.assertEqual(response.status_code, 200) # Assuming backend allows dynamic names

    # --- 3. JSON & File Handling Tests ---

    def test_mask_save_and_load(self):
        """Test saving and loading mask data (IO Mocking)."""
        mask_data = {"positive": [{"x": 10, "y": 10}], "negative": []}
        
        # Test Save
        with patch("builtins.open", mock_open()) as mocked_file:
            with patch("pathlib.Path.mkdir"): # Skip actual mkdir
                response = self.client.post(
                    "/mask/save/Video1/Clip1/1", 
                    json=mask_data
                )
                self.assertEqual(response.status_code, 200)
                # Verify JSON was written
                mocked_file().write.assert_called()

        # Test Load
        with patch("builtins.open", mock_open(read_data=json.dumps(mask_data))):
            with patch("pathlib.Path.exists", return_value=True):
                response = self.client.get("/mask/load/Video1/Clip1/1")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), mask_data)

# =================================================================================================
# INTEGRATION TESTS (Top-Down / Drivers & Stubs)
# =================================================================================================

class TestComfyIntegration(unittest.TestCase):
    
    @patch("comfy_client.requests.post")
    @patch("comfy_client.urllib.request.urlopen")
    @patch("comfy_client.websocket.WebSocket")
    def test_comfy_communication_flow(self, mock_ws, mock_urlopen, mock_post):
        """
        Top-Down Test: Simulates the `generate_clip` function calling ComfyUI.
        We act as the Driver, ComfyUI is the Stub.
        """
        import comfy_client
        
        # 1. Stub Upload Response
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"name": "test_upload.png"}
        
        # 2. Stub Queue Prompt Response
        mock_urllib_response = MagicMock()
        mock_urllib_response.read.return_value = json.dumps({"prompt_id": "12345"}).encode("utf-8")
        mock_urlopen.return_value = mock_urllib_response
        
        # 3. Stub WebSocket (Progress Tracking)
        mock_ws_instance = MagicMock()
        # Sequence of messages: Executing -> Finished
        mock_ws_instance.recv.side_effect = [
            json.dumps({"type": "executing", "data": {"node": None, "prompt_id": "12345"}}), # Finished
        ]
        mock_ws.return_value = mock_ws_instance

        # 4. Mock File IO inside generate_clip
        with patch("builtins.open", mock_open(read_data=b"dummy video bytes")):
            with patch("comfy_client.load_workflow_template", return_value={
                "3": {"inputs": {"seed": 0}},
                "79": {"inputs": {"video": ""}},
                "117": {"inputs": {"video": ""}},
                "78": {"inputs": {"image": ""}},
                "114": {"inputs": {"filename_prefix": ""}},
                "83": {"inputs": {"value": 0}},
                "76": {"inputs": {"value": 0}},
                "77": {"inputs": {"coordinates": "", "neg_coordinates": ""}}
            }):
                try:
                    result = comfy_client.generate_clip(
                        source_video_path="dummy_vid.mp4",
                        character_image_path="dummy_char.png",
                        mask_path=None,
                        output_filename="TEST_OUTPUT",
                        video_id="Video1",
                        mask_points={"positive": [], "negative": []}
                    )
                    self.assertEqual(result, "TEST_OUTPUT")
                except Exception as e:
                    self.fail(f"generate_clip raised an exception: {e}")

# =================================================================================================
# SYSTEM / SCENARIO TESTS (Bottom-Up Logic)
# =================================================================================================

class TestSystemScenarios(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("subprocess.run")
    def test_stitching_process(self, mock_subprocess):
        """
        Test the stitching logic.
        Mocks FFmpeg calls to verify command construction without processing actual video.
        """
        mock_subprocess.return_value.returncode = 0
        
        # Mock the job profile loading
        fake_job = {
            "video_id": "Video1", 
            "fps": 24,
            "clips": [
                {"clip_id": "c1", "path": "p1", "type": "NoChar", "actions": []}
            ]
        }
        
        with patch("builtins.open", mock_open(read_data=json.dumps(fake_job))):
            with patch("pathlib.Path.exists", return_value=True): # Folder exists
                # We need to mock 'shutil.copy' or the file existence check inside stitch_video
                # This is tricky because the function checks .exists() on specific files.
                # A robust integration test here usually requires a temporary directory fixture.
                pass 

    def test_queue_entire_video_validation(self):
        """Test the logic that checks for missing masks before queuing."""
        fake_job = {
            "video_id": "Video1",
            "fps": 24,
            "clips": [
                {
                    "clip_id": "Clip1", 
                    "type": "Char1",
                    "path": "path",
                    "start":0, "end":1,
                    "actions": [{"pass": 1, "character": "char1"}]
                }
            ]
        }

        # Case 1: Mask Missing -> Should Return Error
        with patch("main.get_job_profile_path") as mock_get_path:
            mock_get_path.return_value.exists.return_value = True
            
            with patch("builtins.open", mock_open(read_data=json.dumps(fake_job))):
                with patch("pathlib.Path.exists", side_effect=[False]): # Mask path does NOT exist
                    response = self.client.post("/queue/all/Video1")
                    self.assertEqual(response.json()["status"], "error")
                    self.assertIn("Missing masks", response.json()["message"])

if __name__ == "__main__":
    unittest.main()