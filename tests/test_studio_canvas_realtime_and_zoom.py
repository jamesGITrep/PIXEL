"""
Unit & Integration Tests for Studio Canvas Real-Time Reveal, Tile Zoom Clarity & Streamlined Progress Pill
Verifies:
1. Output Viewport / Result Endpoint: GET /api/v1/jobs/{job_id}/result returns valid PNG with cache-busting headers.
2. High-Res Zoom Clarity: export_resolution (4000, 8000, 12000) yields crisp tile cell dimensions (e.g., 80px per tile for 8000).
3. Real-Time Canvas Refresh: GET /api/v1/jobs/{job_id}/progress-patches returns patch coordinates, colors, and thumbnails.
4. Streamlined Minimalist Progress Bar: Floating progress pill in StudioWorkshop without verbose clutter.
"""

import os
import sys
import time
import io
import json
import unittest
import requests
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


class TestStudioCanvasRealtimeAndZoom(unittest.TestCase):

    def test_high_res_export_resolution_and_patches(self):
        """Verify export_resolution scaling (8000px yielding ~80px tiles) and progress-patches endpoint."""
        buf = io.BytesIO()
        Image.new("RGB", (400, 400), (60, 140, 210)).save(buf, format="JPEG")
        buf.seek(0)

        # Dispatch with High-Res Print (8000px) resolution
        files = {"target_file": ("highres_test.jpg", buf, "image/jpeg")}
        config = {
            "mode": "photomosaic",
            "columns": 50,
            "rows": 50,
            "export_resolution": 8000,
            "use_personal_photos": True,
            "use_community_photos": True,
            "no_repeat_radius": 3
        }

        res = requests.post(
            f"{GATEWAY_URL}/api/v1/jobs/create",
            files=files,
            data={"config_json": json.dumps(config)}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        job_id = data["job_id"]
        manifest = data["manifest"]

        # 1. Verify tile resolution enhancement
        cell_size = manifest["grid"]["cell_size"]
        self.assertTrue(cell_size >= 80, f"Expected tile cell size >= 80px for 8000px export, got {cell_size}px")
        self.assertEqual(manifest["target_dimensions"]["width"], 50 * cell_size)
        print(f"\n[OK] High-Res Zoom Clarity: 8000px export yields {cell_size}px per tile (Canvas: {manifest['target_dimensions']['width']}x{manifest['target_dimensions']['height']})")

        # 2. Poll status and test progress-patches
        completed = False
        start_t = time.time()
        while time.time() - start_t < 35:
            # Test progress-patches endpoint
            patch_res = requests.get(f"{GATEWAY_URL}/api/v1/jobs/{job_id}/progress-patches")
            self.assertEqual(patch_res.status_code, 200)
            patch_data = patch_res.json()
            self.assertIn("job_id", patch_data)
            self.assertIn("patches", patch_data)
            self.assertIn("target_dimensions", patch_data)

            if len(patch_data["patches"]) > 0:
                first_patch = patch_data["patches"][0]
                self.assertIn("cells", first_patch)
                if len(first_patch["cells"]) > 0:
                    c = first_patch["cells"][0]
                    self.assertIn("px", c)
                    self.assertIn("py", c)
                    self.assertIn("w", c)
                    self.assertIn("h", c)
                    self.assertIn("rgb", c)

            # Check status
            status_res = requests.get(f"{GATEWAY_URL}/api/v1/jobs/{job_id}/status")
            status = status_res.json()
            if status.get("is_complete"):
                completed = True
                break
            time.sleep(1.0)

        self.assertTrue(completed, "Job failed to complete within 35 seconds")
        print(f"[OK] Real-time Progress Patches verified for Job {job_id}")

        # 3. Test finished artboard result endpoint (Requirement 1)
        result_res = requests.get(f"{GATEWAY_URL}/api/v1/jobs/{job_id}/result")
        self.assertEqual(result_res.status_code, 200)
        self.assertEqual(result_res.headers.get("content-type"), "image/png")
        self.assertIn("no-cache", result_res.headers.get("cache-control", ""))

        # Verify decoded image dimensions
        out_img = Image.open(io.BytesIO(result_res.content))
        self.assertEqual(out_img.size[0], manifest["target_dimensions"]["width"])
        self.assertEqual(out_img.size[1], manifest["target_dimensions"]["height"])
        print(f"[OK] Master Artboard Result verified: Successfully downloaded {out_img.size[0]}x{out_img.size[1]} PNG directly from /api/v1/jobs/{job_id}/result with no-cache headers.")

    def test_minimalist_progress_bar_and_floating_tiles_code(self):
        """Verify StudioWorkshop and CSS contain streamlined floating pill and floating tile animation."""
        with open("services/frontend/src/components/StudioWorkshop.jsx", "r", encoding="utf-8") as f:
            jsx = f.read()

        # Check floating progress pill
        self.assertIn("floating-progress-pill", jsx)
        self.assertIn("{jobProgress}%", jsx)
        self.assertIn("{completedChunks} / {totalChunks} Chunks", jsx)

        # Redundant verbose texts must be removed from the canvas viewport
        self.assertNotIn("Distributed workers computing CIEDE2000 color delta against photographic asset library", jsx)
        self.assertNotIn("Status: Distributed Workers Computing CIEDE2000 Matching...", jsx)

        # Check Master Canvas Export Resolution in JSX
        self.assertIn("Master Canvas Export Resolution", jsx)
        self.assertIn("Standard Web", jsx)
        self.assertIn("High-Res Print", jsx)
        self.assertIn("Ultra Gigapixel", jsx)

        # Check floating tiles animation overlay
        self.assertIn("tile-fly-in", jsx)

        # Check CSS
        with open("services/frontend/src/styles.css", "r", encoding="utf-8") as f:
            css = f.read()
        self.assertIn("@keyframes tileFlyInAnim", css)
        self.assertIn(".tile-fly-in", css)
        self.assertIn(".floating-progress-pill", css)
        print("[OK] Minimalist floating progress pill and tile fly-in styles verified.")


if __name__ == "__main__":
    unittest.main()
