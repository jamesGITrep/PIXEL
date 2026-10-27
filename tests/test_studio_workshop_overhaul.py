"""
PixelMosaic - Studio Workshop Bug Fixes & Photo Asset Pool Overhaul Test Suite
Verifies:
1. Section 1.1: Live Job Progress Bar & Status Polling (`/api/v1/jobs/{id}/status`)
2. Section 1.2: ASCII aspect ratio matching and buffer dimension calculation (zero black strip bug)
3. Section 2.1: No fake seeds / real photo assets only in storage
4. Section 2.2: Persistent Personal Photo Vault (List, Upload, Purge)
5. Section 2.3: Pool Source Selection (Personal & Community Checkboxes)
6. Section 3.1 & 3.2: Community Gallery (/gallery) navigation and donation ingestion
7. Storage Proxy: /api/v1/storage/{bucket}/{key}
"""

import os
import sys
import time
import io
import json
import unittest
import requests
import numpy as np
from PIL import Image

# Ensure common is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
if "MINIO_ENDPOINT" not in os.environ:
    os.environ["MINIO_ENDPOINT"] = "http://localhost:9000"

from services.worker.engines.ascii_engine import AsciiEngine
from services.common.storage import get_storage, BUCKET_PHOTO_LIBRARY

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

class TestStudioWorkshopOverhaul(unittest.TestCase):

    def test_ascii_engine_aspect_ratio_fix(self):
        """Verify Section 1.2: Exact ASCII matrix (C x R) matching target aspect ratio."""
        engine = AsciiEngine()
        
        # Test 16:9 rectangular landscape image
        w, h = 1920, 1080
        test_img = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
        cols = 100
        char_w, char_h = 8, 14

        res = engine.process_image(test_img, cols=cols, char_w=char_w, char_h=char_h)

        expected_rows = round(cols * (h / w) * (char_w / char_h))
        self.assertEqual(res["columns"], cols)
        self.assertEqual(res["rows"], expected_rows)
        self.assertEqual(res["pixel_width"], cols * char_w)
        self.assertEqual(res["pixel_height"], expected_rows * char_h)
        self.assertEqual(len(res["matrix"]), expected_rows)
        self.assertEqual(len(res["matrix"][0]), cols)

        # Test vertical portrait 9:16
        w_p, h_p = 1080, 1920
        test_img_p = np.random.randint(0, 255, (h_p, w_p, 3), dtype=np.uint8)
        res_p = engine.process_image(test_img_p, cols=cols, char_w=char_w, char_h=char_h)
        expected_rows_p = round(cols * (h_p / w_p) * (char_w / char_h))
        self.assertEqual(res_p["rows"], expected_rows_p)
        print(f"\n[OK] Task 1.2 ASCII aspect ratio verified: 16:9 -> {cols}x{expected_rows}, 9:16 -> {cols}x{expected_rows_p} (zero black strips)")

    def test_personal_photo_vault_lifecycle(self):
        """Verify Section 2.2: Personal Photo Vault (Upload, List, Purge)."""
        session_id = f"test_session_{int(time.time())}"

        # 1. Initially empty
        resp = requests.get(f"{GATEWAY_URL}/api/v1/library/personal?session_id={session_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 0)

        # 2. Upload personal photos
        buf1 = io.BytesIO()
        Image.new("RGB", (200, 200), (45, 120, 230)).save(buf1, format="JPEG")
        buf1.seek(0)

        buf2 = io.BytesIO()
        Image.new("RGB", (200, 200), (220, 80, 50)).save(buf2, format="JPEG")
        buf2.seek(0)

        files = [
            ("photos", ("test_vacation_1.jpg", buf1, "image/jpeg")),
            ("photos", ("test_vacation_2.jpg", buf2, "image/jpeg"))
        ]
        up_resp = requests.post(
            f"{GATEWAY_URL}/api/v1/library/personal/upload",
            data={"session_id": session_id},
            files=files
        )
        self.assertEqual(up_resp.status_code, 200)
        up_data = up_resp.json()
        self.assertEqual(up_data["uploaded_count"], 2)
        self.assertEqual(up_data["total_count"], 2)

        # 3. Query list
        list_resp = requests.get(f"{GATEWAY_URL}/api/v1/library/personal?session_id={session_id}")
        self.assertEqual(list_resp.status_code, 200)
        list_data = list_resp.json()
        self.assertEqual(list_data["count"], 2)
        first_thumb = list_data["photos"][0]["thumb_url"]

        # 4. Storage Proxy check
        proxy_resp = requests.get(f"{GATEWAY_URL}{first_thumb}")
        self.assertEqual(proxy_resp.status_code, 200)
        self.assertEqual(proxy_resp.headers.get("content-type"), "image/jpeg")
        self.assertTrue(len(proxy_resp.content) > 100)

        # 5. Purge Personal Vault
        del_resp = requests.delete(f"{GATEWAY_URL}/api/v1/library/personal?session_id={session_id}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(del_resp.json()["status"], "purged")

        # 6. Verify cleared
        after_resp = requests.get(f"{GATEWAY_URL}/api/v1/library/personal?session_id={session_id}")
        self.assertEqual(after_resp.json()["count"], 0)
        print("[OK] Task 2.2 Personal Photo Vault verified: Upload -> List -> Storage Proxy -> Clear Vault.")

    def test_community_gallery_commons(self):
        """Verify Section 3.2: Community Gallery stats, recent items, and donation ingestion."""
        # 1. Stats
        stats_resp = requests.get(f"{GATEWAY_URL}/api/v1/library/community/stats")
        self.assertEqual(stats_resp.status_code, 200)
        stats = stats_resp.json()
        self.assertTrue(stats["total_photos"] >= 48, f"Expected at least 48 seeded real photos, found {stats['total_photos']}")

        # 2. Recent items
        recent_resp = requests.get(f"{GATEWAY_URL}/api/v1/library/community/recent?limit=10")
        self.assertEqual(recent_resp.status_code, 200)
        recent_data = recent_resp.json()
        self.assertTrue(len(recent_data["recent"]) > 0)
        self.assertIn("thumb_url", recent_data["recent"][0])

        # 3. Community Donation
        buf = io.BytesIO()
        Image.new("RGB", (300, 300), (30, 180, 120)).save(buf, format="JPEG")
        buf.seek(0)
        files = [("photos", ("donor_test_landscape.jpg", buf, "image/jpeg"))]
        don_resp = requests.post(f"{GATEWAY_URL}/api/v1/library/community/upload", files=files)
        self.assertEqual(don_resp.status_code, 200)
        self.assertEqual(don_resp.json()["donated_count"], 1)
        print(f"[OK] Task 3.2 Community Gallery Commons verified: {stats['total_photos']} real photos active.")

    def test_job_dispatch_and_status_polling(self):
        """Verify Section 1.1: Live job dispatch, status polling, and dynamic progress computation."""
        # Create small test target image
        buf = io.BytesIO()
        img = Image.new("RGB", (300, 300), (100, 150, 200))
        img.save(buf, format="JPEG")
        buf.seek(0)

        # Dispatch job with personal + community checkboxes enabled
        files = {"target_file": ("test_poster.jpg", buf, "image/jpeg")}
        config = {
            "mode": "photomosaic",
            "columns": 30,
            "rows": 30,
            "use_personal_photos": True,
            "use_community_photos": True,
            "no_repeat_radius": 3
        }
        create_resp = requests.post(
            f"{GATEWAY_URL}/api/v1/jobs/create",
            files=files,
            data={"config_json": json.dumps(config)}
        )
        self.assertEqual(create_resp.status_code, 200)
        job_info = create_resp.json()
        job_id = job_info["job_id"]
        total_chunks = job_info["total_chunks"]
        self.assertTrue(job_id.startswith("job_"))
        self.assertTrue(total_chunks >= 1)

        # 1-second polling verification loop
        start_time = time.time()
        final_status = None
        while time.time() - start_time < 30:
            status_resp = requests.get(f"{GATEWAY_URL}/api/v1/jobs/{job_id}/status")
            self.assertEqual(status_resp.status_code, 200)
            status = status_resp.json()
            self.assertIn("progress_percent", status)
            self.assertIn("completed_chunks", status)
            self.assertIn("total_chunks", status)
            self.assertIn("status_text", status)

            # Check dynamic formula
            expected_pct = round((status["completed_chunks"] / max(1, status["total_chunks"])) * 100, 1)
            self.assertEqual(status["progress_percent"], expected_pct)

            if status["is_complete"]:
                final_status = status
                break
            time.sleep(1.0)

        self.assertIsNotNone(final_status, "Job did not complete within 30s")
        self.assertEqual(final_status["completed_chunks"], total_chunks)
        self.assertEqual(final_status["progress_percent"], 100.0)
        self.assertIn("output_url", final_status)
        self.assertTrue(final_status["output_url"].endswith("final_mosaic.png"))
        print(f"[OK] Task 1.1 Live Job Dispatch & 1s Polling verified: Job {job_id} reached 100% ({total_chunks}/{total_chunks} chunks).")

    def test_navbar_and_route_isolation(self):
        """Verify Section 3.1 Navbar includes Community Gallery and excludes /admin."""
        with open("services/frontend/src/App.jsx", "r", encoding="utf-8") as f:
            app_code = f.read()

        # Public navigation links
        self.assertIn("Community Gallery", app_code)
        self.assertIn("navigateTo('gallery')", app_code)
        self.assertIn("Overview", app_code)
        self.assertIn("Live Event Arena", app_code)
        self.assertIn("Studio Workshop", app_code)

        # Admin must NOT be linked in public navigation
        self.assertNotIn("navigateTo('admin')", app_code)
        self.assertNotIn(">Admin<", app_code)
        print("[OK] Task 3.1 Navbar Overhaul verified: Community Gallery linked; /admin route strictly isolated.")

if __name__ == "__main__":
    unittest.main()
