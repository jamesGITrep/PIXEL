"""
PixelMosaic - Photomosaic & Personal Vault end-to-end test (runs against the live docker stack).

    docker compose up -d --scale worker=3
    GATEWAY_URL=http://localhost:8000 python -m pytest tests/test_photomosaic_e2e.py -v

Covers: aspect-preserving grid, CIEDE2000 colour placement, cross-chunk no-repeat radius,
empty-pool rejection, Clear Vault purge (and that purged photos can no longer be used),
community tag filtering, and the ASCII / Kuwahara / flow-field render pipeline.
"""

import io
import os
import sys
import time
import uuid
import unittest

import numpy as np
import requests
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
API = f"{GATEWAY_URL}/api/v1"

# One cookie-carrying client; each test signs up a fresh account so its personal vault is its own
HTTP = requests.Session()


def jpeg(arr_or_color, size=(120, 90)) -> bytes:
    if isinstance(arr_or_color, tuple):
        img = Image.new("RGB", size, arr_or_color)
    else:
        img = Image.fromarray(arr_or_color.astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def upload_personal(session, images):
    files = [("photos", (f"p{i}.jpg", data, "image/jpeg")) for i, data in enumerate(images)]
    r = HTTP.post(f"{API}/library/personal/upload", files=files, timeout=60)
    r.raise_for_status()
    return r.json()


def create_job(target_bytes, **config):
    r = HTTP.post(f"{API}/jobs/create",
                      files={"target_file": ("target.jpg", target_bytes, "image/jpeg")},
                      data={"config_json": __import__("json").dumps(config)}, timeout=60)
    return r


def wait_complete(job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = HTTP.get(f"{API}/jobs/{job_id}/status", timeout=10).json()
        if s["is_complete"]:
            return s
        if s.get("is_failed"):
            raise AssertionError(f"Job failed: {s.get('error')}")
        time.sleep(1.0)
    raise AssertionError(f"Job {job_id} did not complete in {timeout}s (last: {s})")


def put_upload(upload_url, body, content_type):
    """PUT a file to the gateway upload relay (upload_url is gateway-relative)."""
    return HTTP.put(f"{GATEWAY_URL}{upload_url}", data=body, headers={"Content-Type": content_type}, timeout=60)


def put_signed(upload_url, body, content_type):
    r = put_upload(upload_url, body, content_type)
    r.raise_for_status()


def wait_for_vault(session, expected, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        listing = HTTP.get(f"{API}/library/personal").json()
        if listing["count"] >= expected and listing["pending_count"] == 0:
            return listing
        time.sleep(0.5)
    raise AssertionError(f"vault never reached {expected} photos: {listing}")


def result_image(job_id):
    r = HTTP.get(f"{API}/jobs/{job_id}/result", timeout=60)
    r.raise_for_status()
    return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))


class TestPhotomosaicE2E(unittest.TestCase):

    def setUp(self):
        HTTP.cookies.clear()
        name = f"e2e_{uuid.uuid4().hex[:10]}"
        r = HTTP.post(f"{API}/auth/signup", json={"username": name, "email": f"{name}@example.com",
                                                   "password": "correct horse battery"}, timeout=30)
        r.raise_for_status()
        self.session = r.json()["user"]["user_id"]  # the vault lives under personal/{user_id}/

    def tearDown(self):
        HTTP.delete(f"{API}/library/personal", timeout=30)

    def test_colour_placement_and_aspect_ratio(self):
        upload_personal(self.session, [jpeg((220, 20, 20)), jpeg((20, 20, 220)), jpeg((20, 200, 20))])
        # 2:1 target: left half red, right half blue
        target = np.zeros((300, 600, 3), dtype=np.uint8)
        target[:, :300] = (230, 10, 10)
        target[:, 300:] = (10, 10, 230)
        r = create_job(jpeg(target), mode="photomosaic", columns=20, export_resolution=1000,
                       use_personal_photos=True, use_community_photos=False,
                       no_repeat_radius=0, alpha_tint_percentage=0)
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        self.assertEqual(job["manifest"]["grid"], {"columns": 20, "rows": 10, "cell_size": 50})
        self.assertEqual(job["manifest"]["pool"]["total"], 3)

        status = wait_complete(job["job_id"])
        self.assertTrue(status["preview_url"])
        img = result_image(job["job_id"])
        self.assertEqual(img.shape[:2], (500, 1000))  # aspect preserved, not squashed square
        left, right = img[:, :450].reshape(-1, 3).mean(0), img[:, 550:].reshape(-1, 3).mean(0)
        self.assertGreater(left[0], 150)
        self.assertLess(left[2], 80)
        self.assertGreater(right[2], 150)
        self.assertLess(right[0], 80)

        preview = HTTP.get(f"{GATEWAY_URL}{status['preview_url']}", timeout=30)
        self.assertEqual(preview.headers["content-type"], "image/jpeg")
        dl = HTTP.get(f"{API}/jobs/{job['job_id']}/result", params={"download": "true"}, timeout=30)
        self.assertIn("attachment", dl.headers["content-disposition"])

    def test_no_repeat_radius_holds_across_chunks(self):
        rng = np.random.default_rng(7)
        photos = [jpeg(tuple(int(v) for v in rng.integers(90, 170, 3))) for _ in range(40)]
        upload_personal(self.session, photos)
        target = np.full((400, 200, 3), 128, dtype=np.uint8)  # uniform grey: every tile wants the same photo
        radius = 3
        r = create_job(jpeg(target), mode="photomosaic", columns=20, export_resolution=1000,
                       use_personal_photos=True, use_community_photos=False,
                       no_repeat_radius=radius, alpha_tint_percentage=0)
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        self.assertGreater(job["total_chunks"], 2, "test needs several row-band chunks")
        wait_complete(job["job_id"])

        patches = HTTP.get(f"{API}/jobs/{job['job_id']}/progress-patches", timeout=30).json()["patches"]
        placed = [(c["col"], c["row"], c["thumb"]) for p in patches for c in p["cells"]]
        self.assertEqual(len(placed), job["manifest"]["total_cells"])
        for i, (c1, r1, k1) in enumerate(placed):
            for c2, r2, k2 in placed[i + 1:]:
                if k1 == k2:
                    self.assertGreaterEqual(max(abs(c1 - c2), abs(r1 - r2)), radius,
                                            f"{k1} repeated at ({c1},{r1}) and ({c2},{r2})")

    def test_empty_pool_rejected(self):
        r = create_job(jpeg((100, 100, 100)), mode="photomosaic", columns=10,
                       use_personal_photos=True, use_community_photos=False)
        self.assertEqual(r.status_code, 400)
        self.assertIn("empty", r.json()["detail"])

    def test_clear_vault(self):
        upload_personal(self.session, [jpeg((200, 100, 50)), jpeg((50, 100, 200))])
        listing = HTTP.get(f"{API}/library/personal").json()
        self.assertEqual(listing["count"], 2)

        r = HTTP.delete(f"{API}/library/personal")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["deleted_photos"], 2)
        self.assertEqual(body["remaining_count"], 0)
        self.assertEqual(body["deleted_count"], 8)  # raw + tile + thumb + index entry per photo

        listing = HTTP.get(f"{API}/library/personal").json()
        self.assertEqual(listing["count"], 0)
        # The purged photos must no longer be usable for a mosaic
        r = create_job(jpeg((100, 100, 100)), mode="photomosaic", columns=10,
                       use_personal_photos=True, use_community_photos=False)
        self.assertEqual(r.status_code, 400)
        # Clearing an already-empty vault is a harmless no-op
        r = HTTP.delete(f"{API}/library/personal")
        self.assertEqual(r.json()["deleted_photos"], 0)

    def test_community_tag_filter(self):
        from services.common.storage import get_storage, BUCKET_PHOTO_LIBRARY
        tag = f"E2E{uuid.uuid4().hex[:6]}"
        other = f"E2X{uuid.uuid4().hex[:6]}"
        r = HTTP.post(f"{API}/library/community/upload", data={"tags": tag},
                          files=[("photos", ("c.jpg", jpeg((1, 2, 3)), "image/jpeg"))], timeout=30)
        r.raise_for_status()
        photo_id = r.json()["donated_items"][0]["id"]
        try:
            stats = HTTP.get(f"{API}/library/community/stats", params={"tags": tag}).json()
            stats_other = HTTP.get(f"{API}/library/community/stats", params={"tags": other}).json()
            self.assertEqual(stats["tag_counts"].get(tag), 1)
            # BRD 4.3: only photos carrying a selected tag are eligible
            self.assertEqual(stats["filtered_photos"], 1)
            self.assertEqual(stats_other["filtered_photos"], 0)
            # A job filtered to a tag nobody uses is rejected instead of silently using other photos
            r = create_job(jpeg((100, 100, 100)), mode="photomosaic", columns=10, use_personal_photos=False,
                           use_community_photos=True, selected_tags=[other])
            self.assertEqual(r.status_code, 400)
            self.assertIn(other, r.json()["detail"])
        finally:
            st = get_storage()
            for k in st.list_objects(BUCKET_PHOTO_LIBRARY, "public/"):
                if photo_id in k:
                    st.delete_object(BUCKET_PHOTO_LIBRARY, k)

    def test_upload_relay_security(self):
        """Uploads go through the gateway: single-use, ticketed, size- and type-checked."""
        files = [{"filename": f"{i}.jpg", "content_type": "image/jpeg"} for i in range(4)]
        ups = HTTP.post(f"{API}/library/upload-urls", json={
            "pool": "personal", "files": files}).json()["uploads"]
        self.assertTrue(all(u["upload_url"].startswith("/api/v1/library/uploads/") for u in ups))

        good, bad_ticket, not_image, too_big = ups
        self.assertEqual(put_upload(good["upload_url"], jpeg((1, 2, 3)), "image/jpeg").status_code, 200)
        self.assertEqual(put_upload(good["upload_url"], jpeg((1, 2, 3)), "image/jpeg").status_code, 404)  # single use
        forged = bad_ticket["upload_url"].split("?")[0] + "?ticket=forged"
        self.assertEqual(put_upload(forged, jpeg((1, 2, 3)), "image/jpeg").status_code, 403)
        self.assertEqual(put_upload(not_image["upload_url"], b"<html>not an image</html>", "image/jpeg").status_code, 415)
        self.assertEqual(put_upload(too_big["upload_url"], b"\xff\xd8\xff" + b"0" * (31 * 1024 * 1024), "image/jpeg").status_code, 413)
        self.assertEqual(HTTP.put(f"{API}/library/uploads/../../x?ticket=a", data=b"x").status_code, 404)
        wait_for_vault(self.session, 1)

    def test_live_event_state_survives_across_gateways(self):
        """Event state lives in the bucket, so any gateway VM (or a restarted one) serves it."""
        event_id = f"evt_e2e_{uuid.uuid4().hex[:6]}"
        tokens = []
        for color in [(200, 50, 50), (50, 200, 50)]:
            r = HTTP.post(f"{API}/events/{event_id}/upload",
                              files={"photo": ("p.jpg", jpeg(color), "image/jpeg")})
            self.assertEqual(r.status_code, 200, r.text)
            tokens.append(r.json()["contributor_token"])
        self.assertEqual(len(set(tokens)), 2)
        patches = HTTP.get(f"{API}/events/{event_id}/patches").json()
        self.assertEqual(patches["photos_placed"], 2)
        thumb = patches["patches"][0]["image_url"]
        self.assertEqual(HTTP.get(f"{GATEWAY_URL}{thumb}").status_code, 200)
        etag = HTTP.get(f"{API}/events/{event_id}/patches").headers["ETag"]
        self.assertEqual(HTTP.get(f"{API}/events/{event_id}/patches", headers={"If-None-Match": etag}).status_code, 304)

        # A fresh gateway process has no memory of the event; it must answer from the bucket
        import importlib, services.gateway.main as gw
        from fastapi.testclient import TestClient
        fresh = TestClient(importlib.reload(gw).app)
        found = fresh.get(f"/api/v1/events/{event_id}/find/{tokens[1].lstrip('#')}")
        self.assertEqual(found.status_code, 200, found.text)
        self.assertEqual(found.json()["token"], tokens[1])
        self.assertEqual(fresh.get(f"/api/v1/events/{event_id}/find/T-0000").status_code, 404)

        from services.common.storage import get_storage, BUCKET_PHOTO_LIBRARY, BUCKET_MOSAIC_OUTPUTS
        get_storage().delete_prefix(BUCKET_MOSAIC_OUTPUTS, f"events/{event_id}/")
        get_storage().delete_prefix(BUCKET_PHOTO_LIBRARY, f"events/{event_id}/")

    def test_direct_signed_url_ingestion(self):
        """BRD 4.1: drag-and-drop uploads reach the private bucket via the gateway; workers analyse and index them."""
        files = [{"filename": "a.jpg", "content_type": "image/jpeg"},
                 {"filename": "b.png", "content_type": "image/png"}]
        r = HTTP.post(f"{API}/library/upload-urls", json={
            "pool": "personal", "tags": ["Nature"], "files": files})
        self.assertEqual(r.status_code, 200, r.text)
        uploads = r.json()["uploads"]
        self.assertEqual(len(uploads), 2)
        png = io.BytesIO()
        Image.new("RGB", (80, 60), (10, 200, 10)).save(png, format="PNG")
        bodies = [jpeg((200, 10, 10)), png.getvalue()]
        for up, body in zip(uploads, bodies):
            put_signed(up["upload_url"], body, up["content_type"])

        listing = wait_for_vault(self.session, 2)
        self.assertEqual(listing["pending_count"], 0)
        self.assertTrue(all(p["tags"] == ["Nature"] for p in listing["photos"]))

        from services.common.storage import get_storage, BUCKET_PHOTO_LIBRARY
        st = get_storage()
        index_keys = st.list_objects(BUCKET_PHOTO_LIBRARY, f"personal/{self.session}/index/")
        self.assertEqual(len(index_keys), 2)  # analysed and added to the photo index
        self.assertFalse(st.list_objects(BUCKET_PHOTO_LIBRARY, f"incoming/personal/{self.session}/"))

        # Strict tag filter applies to the personal pool too
        r = create_job(jpeg((100, 100, 100)), mode="photomosaic", columns=10, use_personal_photos=True,
                       use_community_photos=False, selected_tags=["Vehicles"])
        self.assertEqual(r.status_code, 400)
        r = create_job(jpeg((100, 100, 100)), mode="photomosaic", columns=10, use_personal_photos=True,
                       use_community_photos=False, selected_tags=["Nature"])
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["manifest"]["pool"]["personal"], 2)

        # Bad requests
        self.assertEqual(HTTP.post(f"{API}/library/upload-urls", json={
            "pool": "nope", "files": files}).status_code, 400)

    def test_blend_ratio(self):
        """BRD 4.2: the personal/community slider sets the share of cells using personal photos."""
        community = HTTP.get(f"{API}/library/community/stats").json()["total_photos"]
        if community == 0:
            self.skipTest("community pool empty")
        rng = np.random.default_rng(11)
        upload_personal(self.session, [jpeg(tuple(int(v) for v in rng.integers(0, 255, 3))) for _ in range(12)])
        target = np.asarray(Image.new("RGB", (300, 200), (120, 90, 160)))
        for ratio in (0.3, 0.8):
            with self.subTest(ratio=ratio):
                r = create_job(jpeg(target), mode="photomosaic", columns=15, export_resolution=1000,
                               use_personal_photos=True, use_community_photos=True,
                               blend_ratio_personal=ratio,
                               no_repeat_radius=2, alpha_tint_percentage=0)
                self.assertEqual(r.status_code, 200, r.text)
                job = r.json()
                wait_complete(job["job_id"])
                patches = HTTP.get(f"{API}/jobs/{job['job_id']}/progress-patches").json()["patches"]
                sources = [c["source"] for p in patches for c in p["cells"]]
                share = sources.count("personal") / len(sources)
                self.assertAlmostEqual(share, ratio, delta=0.03)

    def test_tint_adjustable_after_completion(self):
        """BRD 4.5: the stored artboard is untinted; the slider value is applied on view/export."""
        upload_personal(self.session, [jpeg((20, 20, 220))])
        target = np.asarray(Image.new("RGB", (200, 200), (230, 30, 30)))
        r = create_job(jpeg(target), mode="photomosaic", columns=5, export_resolution=1000,
                       use_personal_photos=True, use_community_photos=False,
                       no_repeat_radius=0, alpha_tint_percentage=20)
        job_id = r.json()["job_id"]
        wait_complete(job_id)

        def mean_red(tint=None):
            params = {} if tint is None else {"tint": tint}
            img = Image.open(io.BytesIO(HTTP.get(f"{API}/jobs/{job_id}/result", params=params).content))
            return np.asarray(img.convert("RGB"))[..., 0].mean()

        r0, r_default, r50 = mean_red(0), mean_red(), mean_red(50)
        self.assertLess(r0, 40)                      # pure blue tiles
        self.assertAlmostEqual(r_default, 0.8 * r0 + 0.2 * 230, delta=6)   # job default 20%
        self.assertAlmostEqual(r50, 0.5 * r0 + 0.5 * 230, delta=6)

    def test_admin_telemetry_is_live(self):
        """BRD 4.6: dashboard data comes from real worker heartbeats."""
        upload_personal(self.session, [jpeg((i * 40, 100, 200 - i * 40)) for i in range(5)])
        r = create_job(jpeg((120, 120, 120)), mode="photomosaic", columns=30, export_resolution=1000,
                       use_personal_photos=True, use_community_photos=False,
                       no_repeat_radius=0)
        wait_complete(r.json()["job_id"])
        time.sleep(4)  # one heartbeat interval
        t = HTTP.get(f"{API}/admin/telemetry").json()
        self.assertGreaterEqual(t["summary"]["active_nodes"], 1)
        self.assertEqual(len(t["nodes"]), t["summary"]["active_nodes"])
        self.assertGreater(t["summary"]["tiles_per_sec"], 0)
        self.assertGreater(t["summary"]["cells_total"], 0)
        self.assertTrue(t["history"])
        self.assertTrue(all("process_cpu_percent" in n for n in t["nodes"]))

    def test_other_engines_render(self):
        rng = np.random.default_rng(1)
        target = rng.integers(0, 255, (240, 320, 3)).astype(np.uint8)
        target[:120] = (240, 200, 40)
        for mode in ("ascii", "kuwahara", "flowfield"):
            with self.subTest(mode=mode):
                r = create_job(jpeg(target), mode=mode, columns=60, kuwahara_radius=3, num_particles=800)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["total_chunks"], 1)
                wait_complete(r.json()["job_id"], timeout=120)
                img = result_image(r.json()["job_id"])
                self.assertGreater(img.mean(), 5, f"{mode} output is blank")
                if mode != "ascii":
                    self.assertAlmostEqual(img.shape[1] / img.shape[0], 320 / 240, places=1)

    def test_true_color_ascii_uses_source_colours(self):
        target = np.zeros((240, 320, 3), np.uint8)
        target[:, :160] = (230, 40, 40)    # red left half (RGB)
        target[:, 160:] = (40, 60, 230)    # blue right half
        r = create_job(jpeg(target), mode="ascii", columns=60, ascii_theme="truecolor")
        self.assertEqual(r.status_code, 200, r.text)
        wait_complete(r.json()["job_id"], timeout=120)
        img = result_image(r.json()["job_id"]).astype(int)
        h, w = img.shape[:2]
        glyph = img.sum(axis=2) > 120  # drawn pixels (background is near-black)
        left, right = img[:, : w // 2][glyph[:, : w // 2]], img[:, w // 2:][glyph[:, w // 2:]]
        self.assertGreater(len(left), 100)
        self.assertGreater(len(right), 100)
        self.assertGreater(left[:, 0].mean(), left[:, 2].mean() + 80)    # glyphs are red on the left
        self.assertGreater(right[:, 2].mean(), right[:, 0].mean() + 80)  # and blue on the right


if __name__ == "__main__":
    unittest.main(verbosity=2)
