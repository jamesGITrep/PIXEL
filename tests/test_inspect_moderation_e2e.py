"""
Tile inspector, contributor tracking, high-res stitching and community moderation, end to end.

    docker compose up -d
    GATEWAY_URL=http://localhost:8000 python -m unittest tests.test_inspect_moderation_e2e -v

The local stacks set ADMIN_USERNAMES=pm_admin, so the test signs up (or signs in) as pm_admin.
"""

import io
import json
import os
import sys
import time
import unittest
import uuid

import numpy as np
import requests
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.common.photo_library import render_tile_candidates

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
API = f"{GATEWAY_URL}/api/v1"
PASSWORD = "correct horse battery"
ADMIN = "pm_admin"


def jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def solid(color, size=(600, 400)):
    return jpeg_bytes(Image.new("RGB", size, color))


def checkerboard(period=4, size=800, colors=((250, 250, 250), (10, 10, 10))):
    yy, xx = np.mgrid[0:size, 0:size]
    mask = ((xx // period) + (yy // period)) % 2 == 0
    arr = np.where(mask[..., None], np.array(colors[0]), np.array(colors[1])).astype(np.uint8)
    return jpeg_bytes(Image.fromarray(arr))


def account(name=None):
    c = requests.Session()
    name = name or f"insp_{uuid.uuid4().hex[:10]}"
    r = c.post(f"{API}/auth/signup", json={"username": name, "email": f"{name}@example.com", "password": PASSWORD})
    if r.status_code == 409:
        r = c.post(f"{API}/auth/login", json={"identifier": name, "password": PASSWORD})
    r.raise_for_status()
    return c, r.json()["user"]


def upload(client, pool, bodies, tags=None):
    files = [{"filename": f"p{i}.jpg", "content_type": "image/jpeg"} for i in range(len(bodies))]
    r = client.post(f"{API}/library/upload-urls", json={"pool": pool, "tags": tags or [], "files": files}, timeout=30)
    r.raise_for_status()
    ups = r.json()["uploads"]
    for up, body in zip(ups, bodies):
        client.put(f"{GATEWAY_URL}{up['upload_url']}", data=body, headers={"Content-Type": "image/jpeg"},
                   timeout=60).raise_for_status()
    return [u["photo_id"] for u in ups]


def wait_ingested(client, pool, ids, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pool == "personal":
            have = {p["id"] for p in client.get(f"{API}/library/personal").json()["photos"]}
        else:
            have = {p["id"] for p in requests.get(f"{API}/library/community/recent", params={"limit": 100000}).json()["recent"]}
        if set(ids) <= have:
            return
        time.sleep(0.5)
    raise AssertionError(f"{pool} uploads not ingested in time")


def run_job(client, target_bytes, **config):
    r = client.post(f"{API}/jobs/create", files={"target_file": ("t.jpg", target_bytes, "image/jpeg")},
                    data={"config_json": json.dumps(config)}, timeout=60)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        s = client.get(f"{API}/jobs/{job_id}/status").json()
        if s["is_complete"]:
            return job_id
        if s.get("is_failed"):
            raise AssertionError(s.get("error"))
        time.sleep(1)
    raise AssertionError("job did not finish")


class TestTileInspector(unittest.TestCase):

    def test_inspect_tile_shows_original_contributor_and_score(self):
        c, user = account()
        ids = upload(c, "personal", [solid((220, 30, 30)), solid((30, 30, 220))], tags=["Nature"])
        wait_ingested(c, "personal", ids)
        job = run_job(c, solid((200, 40, 40), (300, 300)), mode="photomosaic", columns=6,
                      use_personal_photos=True, use_community_photos=False, no_repeat_radius=0)

        patches = c.get(f"{API}/jobs/{job}/progress-patches").json()["patches"]
        cells = [cell for p in patches for cell in p["cells"]]
        self.assertEqual(len(cells), 36)
        self.assertTrue(all(cell["cell_index"] is not None and cell["delta_e"] is not None for cell in cells))

        info = requests.get(f"{API}/jobs/{job}/cells/{cells[0]['cell_index']}").json()
        self.assertEqual(info["contributor"], user["username"])
        self.assertEqual(info["source"], "personal")
        self.assertEqual(info["tags"], ["Nature"])
        self.assertIn(info["photo_id"], ids)
        self.assertLess(info["delta_e"], 10)  # red target, red photo

        orig = requests.get(f"{GATEWAY_URL}{info['original_url']}")
        self.assertEqual(orig.status_code, 200)
        self.assertEqual(orig.headers["content-type"], "image/jpeg")
        self.assertEqual(Image.open(io.BytesIO(orig.content)).size, (600, 400))  # original aspect, not a 50px thumb

        self.assertEqual(requests.get(f"{API}/jobs/{job}/cells/99999").status_code, 404)
        self.assertEqual(requests.get(f"{API}/jobs/..%2F..%2Fx/cells/0").status_code, 404)
        c.delete(f"{API}/library/personal")

    def test_high_res_cells_use_the_original_photo(self):
        # 400px cells (> 256px tiles): stitched from the 800px original, so a 4px checkerboard survives
        c, _ = account()
        ids = upload(c, "personal", [checkerboard()])
        wait_ingested(c, "personal", ids)
        job = run_job(c, solid((128, 128, 128), (400, 400)), mode="photomosaic", columns=10,
                      export_resolution=4000, use_personal_photos=True, use_community_photos=False, no_repeat_radius=0)
        img = np.asarray(Image.open(io.BytesIO(requests.get(f"{API}/jobs/{job}/result", params={"tint": 0}).content)).convert("L"), dtype=np.float64)
        self.assertEqual(img.shape, (4000, 4000))
        cell = img[20:380, 20:380]
        # Neighbouring 2px squares stay distinct (an upscaled 256px tile blurs them to grey)
        self.assertGreater(np.abs(np.diff(cell, axis=1)).mean(), 40)
        c.delete(f"{API}/library/personal")

    def test_tile_source_order(self):
        key = "public/thumbs-50x50/abc.jpg"
        self.assertEqual(render_tile_candidates(key, 120)[0], "public/tiles-256/abc.jpg")
        self.assertEqual(render_tile_candidates(key, 400)[0], "public/raw/abc.jpg")


class TestModeration(unittest.TestCase):

    def test_admin_can_list_and_delete_community_photos(self):
        donor, donor_user = account()
        ids = upload(donor, "community", [solid((10, 200, 10), (300, 300))], tags=["Textures"])
        wait_ingested(donor, "community", ids)
        photo_id = ids[0]

        # Guests and ordinary accounts can't moderate
        self.assertEqual(requests.get(f"{API}/admin/community/photos").status_code, 401)
        self.assertEqual(donor.get(f"{API}/admin/community/photos").status_code, 403)
        self.assertEqual(donor.delete(f"{API}/admin/community/photos/{photo_id}").status_code, 403)

        admin, admin_user = account(ADMIN)
        self.assertTrue(admin.get(f"{API}/auth/me").json()["user"]["is_admin"])
        listing = admin.get(f"{API}/admin/community/photos", params={"limit": 200}).json()
        total_before = listing["total"]
        found = [p for p in listing["photos"] if p["id"] == photo_id]
        offset = 200
        while not found and offset < total_before:
            page = admin.get(f"{API}/admin/community/photos", params={"offset": offset, "limit": 200}).json()
            found = [p for p in page["photos"] if p["id"] == photo_id]
            offset += 200
        self.assertTrue(found, "donated photo missing from the moderation list")
        self.assertEqual(found[0]["contributor"], donor_user["username"])
        self.assertEqual(found[0]["tags"], ["Textures"])

        r = admin.delete(f"{API}/admin/community/photos/{photo_id}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(r.json()["deleted_objects"], 5)  # raw, tile, thumb, index, tag marker
        self.assertEqual(r.json()["total_photos"], total_before - 1)
        self.assertEqual(admin.delete(f"{API}/admin/community/photos/{photo_id}").status_code, 404)
        self.assertEqual(admin.delete(f"{API}/admin/community/photos/bad.id").status_code, 400)
        stats = requests.get(f"{API}/library/community/stats", params={"tags": "Textures"}).json()
        self.assertNotIn(photo_id, json.dumps(stats))


if __name__ == "__main__":
    unittest.main(verbosity=2)
