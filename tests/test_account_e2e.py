"""
Account controls (/account), end to end: summary, selective vault delete, and the full purge.

    docker compose up -d
    GATEWAY_URL=http://gateway:8000 python -m unittest tests.test_account_e2e -v   (on the data network,
    with the gateway's storage env, so the test can confirm objects are really gone from the bucket)
"""

import io
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


def jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, format="JPEG")
    return buf.getvalue()


def new_account():
    c = requests.Session()
    name = f"acct_{uuid.uuid4().hex[:10]}"
    r = c.post(f"{API}/auth/signup", json={"username": name, "email": f"{name}@example.com", "password": PASSWORD})
    r.raise_for_status()
    return c, r.json()["user"]


def fill_vault(c, colors):
    files = [{"filename": f"p{i}.jpg", "content_type": "image/jpeg"} for i in range(len(colors))]
    ups = c.post(f"{API}/library/upload-urls", json={"pool": "personal", "tags": ["Nature"], "files": files}).json()["uploads"]
    for up, color in zip(ups, colors):
        c.put(f"{GATEWAY_URL}{up['upload_url']}", data=jpeg(color), headers={"Content-Type": "image/jpeg"}).raise_for_status()
    deadline = time.time() + 60
    while time.time() < deadline:
        listing = c.get(f"{API}/library/personal").json()
        if listing["count"] >= len(colors) and listing["pending_count"] == 0:
            return [p["id"] for p in listing["photos"]]
        time.sleep(0.5)
    raise AssertionError("vault not ingested")


def bucket_keys(prefix):
    from services.common.storage import get_storage, BUCKET_PHOTO_LIBRARY
    return get_storage().list_objects(BUCKET_PHOTO_LIBRARY, prefix)


def account_keys(username):
    from services.common.storage import get_storage, BUCKET_TASK_COORDINATION
    return get_storage().object_exists(BUCKET_TASK_COORDINATION, f"users/{username}.json")


class TestAccountPage(unittest.TestCase):

    def test_summary_and_selective_delete(self):
        c, user = new_account()
        ids = fill_vault(c, [(200, 0, 0), (0, 200, 0), (0, 0, 200)])

        s = c.get(f"{API}/users/me").json()
        self.assertEqual(s["user"]["username"], user["username"])
        self.assertEqual(s["user"]["email"], user["email"])
        self.assertTrue(s["user"]["created_at"].endswith("Z"))
        self.assertEqual(s["photo_count"], 3)
        self.assertGreater(s["bytes_used"], 0)
        self.assertEqual(s["quota_bytes"], 500 * 1024 * 1024)

        victim = ids[0]
        r = c.post(f"{API}/library/personal/delete", json={"photo_ids": [victim, "nope123"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"], [victim])
        self.assertEqual(r.json()["not_found"], ["nope123"])
        self.assertEqual(c.get(f"{API}/users/me").json()["photo_count"], 2)
        self.assertEqual([k for k in bucket_keys(f"personal/{user['user_id']}/") if victim in k], [])  # every variant + tag marker
        self.assertLess(c.get(f"{API}/users/me").json()["bytes_used"], s["bytes_used"])

        self.assertEqual(c.post(f"{API}/library/personal/delete", json={"photo_ids": ["../x"]}).status_code, 400)
        self.assertEqual(c.post(f"{API}/library/personal/delete", json={"photo_ids": []}).status_code, 400)
        guest = requests.Session()
        self.assertEqual(guest.get(f"{API}/users/me").status_code, 401)
        self.assertEqual(guest.post(f"{API}/library/personal/delete", json={"photo_ids": [ids[1]]}).status_code, 404)  # a guest has no vault, so nothing of the account is reachable
        c.delete(f"{API}/library/personal")

    def test_purge_requires_confirmation_and_removes_everything(self):
        c, user = new_account()
        fill_vault(c, [(10, 10, 10), (240, 240, 240)])
        other = requests.Session()  # a second device signed in to the same account
        other.post(f"{API}/auth/login", json={"identifier": user["username"], "password": PASSWORD}).raise_for_status()

        self.assertEqual(c.delete(f"{API}/users/me/purge", json={"confirm": "wrong"}).status_code, 403)
        self.assertEqual(c.delete(f"{API}/users/me/purge", json={"confirm": ""}).status_code, 403)
        self.assertEqual(requests.delete(f"{API}/users/me/purge", json={"confirm": user["username"]}).status_code, 401)

        r = c.delete(f"{API}/users/me/purge", json={"confirm": user["username"].upper()})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted_photos"], 2)

        self.assertEqual(bucket_keys(f"personal/{user['user_id']}/"), [])
        self.assertEqual(bucket_keys(f"incoming/personal/{user['user_id']}/"), [])
        self.assertFalse(account_keys(user["username"]))
        self.assertIsNone(c.get(f"{API}/auth/me").json()["user"])      # cookie cleared
        self.assertIsNone(other.get(f"{API}/auth/me").json()["user"])  # every other session is dead too
        self.assertEqual(requests.post(f"{API}/auth/login", json={"identifier": user["username"],
                                                                   "password": PASSWORD}).status_code, 401)
        # The username and email are free again
        again = requests.post(f"{API}/auth/signup", json={"username": user["username"], "email": user["email"],
                                                          "password": PASSWORD})
        self.assertEqual(again.status_code, 200, again.text)
        self.assertNotEqual(again.json()["user"]["user_id"], user["user_id"])

    def test_purge_with_password(self):
        c, user = new_account()
        r = c.delete(f"{API}/users/me/purge", json={"confirm": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted_photos"], 0)
        self.assertFalse(account_keys(user["username"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
