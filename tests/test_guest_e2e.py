"""
Guests (not signed in) can build mosaics from their own photos; the vault is temporary and moves
into the account if they sign in. Run on the data network with the gateway's storage env:

    GATEWAY_URL=http://gateway:8000 python -m unittest tests.test_guest_e2e -v
"""

import io
import json
import os
import sys
import time
import unittest
import uuid

import requests
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
API = f"{GATEWAY_URL}/api/v1"
PASSWORD = "correct horse battery"


def jpeg(color, size=(300, 200)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def upload(c, colors):
    files = [{"filename": f"g{i}.jpg", "content_type": "image/jpeg"} for i in range(len(colors))]
    r = c.post(f"{API}/library/upload-urls", json={"pool": "personal", "tags": ["Nature"], "files": files})
    r.raise_for_status()
    for up, color in zip(r.json()["uploads"], colors):
        c.put(f"{GATEWAY_URL}{up['upload_url']}", data=jpeg(color), headers={"Content-Type": "image/jpeg"}).raise_for_status()
    deadline = time.time() + 60
    while time.time() < deadline:
        v = c.get(f"{API}/library/personal").json()
        if v["count"] >= len(colors) and v["pending_count"] == 0:
            return v
        time.sleep(0.5)
    raise AssertionError("guest uploads not ingested")


def bucket_keys(prefix):
    from services.common.storage import get_storage, BUCKET_PHOTO_LIBRARY
    return get_storage().list_objects(BUCKET_PHOTO_LIBRARY, prefix)


class TestGuestVault(unittest.TestCase):

    def test_guest_can_make_a_mosaic_from_own_photos(self):
        g = requests.Session()
        r = g.post(f"{API}/library/upload-urls", json={"pool": "personal", "files": [{"filename": "a.jpg", "content_type": "image/jpeg"}]})
        self.assertEqual(r.status_code, 200, r.text)
        cookie = next(ck for ck in g.cookies if ck.name == "pm_guest")
        self.assertTrue(cookie.value.startswith("gst_"))
        self.assertTrue(cookie.has_nonstandard_attr("HttpOnly"))
        self.assertIsNone(cookie.expires)  # session cookie: gone when the browser closes

        listing = upload(g, [(220, 30, 30), (30, 30, 220)])
        self.assertTrue(listing["guest"])
        self.assertEqual(listing["session_id"], cookie.value)

        r = g.post(f"{API}/jobs/create", files={"target_file": ("t.jpg", jpeg((210, 40, 40)), "image/jpeg")},
                   data={"config_json": json.dumps({"mode": "photomosaic", "columns": 8,
                                                     "use_personal_photos": True, "use_community_photos": False})})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["manifest"]["pool"]["personal"], 2)

        # Another browser has its own empty vault and can't reach this one
        other = requests.Session()
        self.assertEqual(other.get(f"{API}/library/personal").json()["count"], 0)
        other.cookies.set("pm_guest", "gst_" + "0" * 24)
        self.assertEqual(other.get(f"{API}/library/personal").json()["count"], 0)
        other.cookies.set("pm_guest", "../personal/x")
        self.assertIsNone(other.get(f"{API}/library/personal").json()["session_id"])  # malformed ids ignored

        self.assertEqual(g.delete(f"{API}/library/personal").json()["deleted_photos"], 2)

    def test_signing_in_moves_guest_photos_into_the_account(self):
        g = requests.Session()
        listing = upload(g, [(10, 200, 10), (200, 200, 10), (10, 10, 10)])
        gid = listing["session_id"]
        self.assertEqual(len(bucket_keys(f"personal/{gid}/thumbs-50x50/")), 3)

        name = f"guest_{uuid.uuid4().hex[:10]}"
        r = g.post(f"{API}/auth/signup", json={"username": name, "email": f"{name}@example.com", "password": PASSWORD})
        r.raise_for_status()
        uid = r.json()["user"]["user_id"]
        claim = g.post(f"{API}/library/guest/claim")
        self.assertEqual(claim.json()["moved_photos"], 3)
        self.assertNotIn("pm_guest", g.cookies.get_dict())

        v = g.get(f"{API}/library/personal").json()
        self.assertEqual((v["count"], v["guest"], v["session_id"]), (3, False, uid))
        self.assertTrue(all(p["tags"] == ["Nature"] for p in v["photos"]))
        self.assertEqual(bucket_keys(f"personal/{gid}/"), [])  # guest copy removed

        # The moved photos are credited to the new account in the tile inspector
        job = g.post(f"{API}/jobs/create", files={"target_file": ("t.jpg", jpeg((10, 190, 10)), "image/jpeg")},
                     data={"config_json": json.dumps({"mode": "photomosaic", "columns": 4,
                                                       "use_personal_photos": True, "use_community_photos": False,
                                                       "no_repeat_radius": 0})}).json()["job_id"]
        for _ in range(120):
            if g.get(f"{API}/jobs/{job}/status").json()["is_complete"]:
                break
            time.sleep(1)
        self.assertEqual(g.get(f"{API}/jobs/{job}/cells/0").json()["contributor"], name)

        self.assertEqual(g.post(f"{API}/library/guest/claim").json()["moved_photos"], 0)  # nothing left to move
        g.delete(f"{API}/users/me/purge", json={"confirm": name})


if __name__ == "__main__":
    unittest.main(verbosity=2)
