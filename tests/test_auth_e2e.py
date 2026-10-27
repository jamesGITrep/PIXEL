"""
Accounts + persistent personal vault, end to end against the running stack.

    docker compose up -d
    GATEWAY_URL=http://localhost:8000 python -m unittest tests.test_auth_e2e -v

The local stack sets AUTH_DEV_RETURN_RESET_LINK=true so the forgot-password stub hands the reset
link back in the response (in GCP it only goes to the gateway log).
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


def new_name():
    return f"auth_{uuid.uuid4().hex[:10]}"


def signup(client, name=None, email=None, password=PASSWORD):
    name = name or new_name()
    return client.post(f"{API}/auth/signup", json={"username": name, "email": email or f"{name}@example.com",
                                                    "password": password}, timeout=30)


def login(client, identifier, password=PASSWORD):
    return client.post(f"{API}/auth/login", json={"identifier": identifier, "password": password}, timeout=30)


def jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="JPEG")
    return buf.getvalue()


def upload_to_vault(client, colors):
    files = [{"filename": f"p{i}.jpg", "content_type": "image/jpeg"} for i in range(len(colors))]
    r = client.post(f"{API}/library/upload-urls", json={"pool": "personal", "files": files}, timeout=30)
    r.raise_for_status()
    for up, color in zip(r.json()["uploads"], colors):
        client.put(f"{GATEWAY_URL}{up['upload_url']}", data=jpeg(color),
                   headers={"Content-Type": "image/jpeg"}, timeout=60).raise_for_status()
    deadline = time.time() + 60
    while time.time() < deadline:
        listing = client.get(f"{API}/library/personal", timeout=30).json()
        if listing["count"] >= len(colors) and listing["pending_count"] == 0:
            return listing
        time.sleep(0.5)
    raise AssertionError("Vault uploads were not ingested in time")


class TestAccounts(unittest.TestCase):

    def test_signup_me_logout_login(self):
        c = requests.Session()
        name = new_name()
        r = signup(c, name, email=f"{name.upper()}@Example.com")
        self.assertEqual(r.status_code, 200, r.text)
        user = r.json()["user"]
        self.assertTrue(user["user_id"].startswith("usr_"))
        self.assertEqual(user["email"], f"{name}@example.com")  # normalised
        self.assertNotIn("password_hash", user)

        cookie = next(ck for ck in c.cookies if ck.name == "pm_session")
        self.assertTrue(cookie.has_nonstandard_attr("HttpOnly"))
        self.assertEqual(c.get(f"{API}/auth/me").json()["user"]["username"], name)

        c.post(f"{API}/auth/logout")
        self.assertEqual(c.get(f"{API}/auth/me").json()["user"], None)

        self.assertEqual(login(c, name).status_code, 200)
        self.assertEqual(login(requests.Session(), f"{name}@EXAMPLE.com").status_code, 200)  # by email
        self.assertEqual(login(requests.Session(), name, "wrong password!").status_code, 401)
        self.assertEqual(login(requests.Session(), new_name()).status_code, 401)  # unknown user

    def test_signup_validation_and_uniqueness(self):
        name = new_name()
        self.assertEqual(signup(requests.Session(), name).status_code, 200)
        self.assertEqual(signup(requests.Session(), name, email=f"{new_name()}@example.com").status_code, 409)
        self.assertEqual(signup(requests.Session(), new_name(), email=f"{name}@example.com").status_code, 409)
        self.assertEqual(signup(requests.Session(), "ab").status_code, 400)
        self.assertEqual(signup(requests.Session(), "../etc").status_code, 400)
        self.assertEqual(signup(requests.Session(), email="not-an-email").status_code, 400)
        self.assertEqual(signup(requests.Session(), password="short").status_code, 400)

    def test_forged_or_tampered_cookie_rejected(self):
        c = requests.Session()
        signup(c)
        token = c.cookies.get("pm_session")
        payload, sig = token.split(".")
        forged = requests.Session()
        forged.cookies.set("pm_session", f"{payload}.{'A' * len(sig)}")
        self.assertEqual(forged.get(f"{API}/auth/me").json()["user"], None)
        forged.cookies.set("pm_session", "garbage")
        forged_view = forged.get(f"{API}/library/personal").json()  # treated as a guest with no vault
        self.assertEqual((forged_view["count"], forged_view["guest"]), (0, True))

    def test_lockout_after_repeated_failures(self):
        name = new_name()
        signup(requests.Session(), name)
        for _ in range(10):
            self.assertEqual(login(requests.Session(), name, "wrong password!").status_code, 401)
        # Locked: even the right password is refused for now
        self.assertEqual(login(requests.Session(), name).status_code, 429)


class TestVaultFollowsAccount(unittest.TestCase):

    def test_vault_persists_across_sessions_and_is_private(self):
        name = new_name()
        a = requests.Session()
        signup(a, name)
        upload_to_vault(a, [(200, 30, 30), (30, 200, 30)])

        # A different device (fresh client, no shared state) logs in and sees the same vault
        b = requests.Session()
        self.assertEqual(login(b, name).status_code, 200)
        listing = b.get(f"{API}/library/personal").json()
        self.assertEqual(listing["count"], 2)
        self.assertEqual(listing["session_id"], a.get(f"{API}/auth/me").json()["user"]["user_id"])

        # Another account has its own, empty vault and can't name someone else's
        other = requests.Session()
        signup(other)
        self.assertEqual(other.get(f"{API}/library/personal", params={"session_id": listing["session_id"]}).json()["count"], 0)

        # Signed-out visitors only ever see their own (empty, temporary) guest vault; clearing it
        # can't touch the account's photos
        guest = requests.Session()
        listing_g = guest.get(f"{API}/library/personal").json()
        self.assertEqual((listing_g["count"], listing_g["guest"]), (0, True))
        self.assertEqual(guest.delete(f"{API}/library/personal").json()["deleted_photos"], 0)
        self.assertEqual(a.get(f"{API}/library/personal").json()["count"], 2)

        # A job from device B uses the vault uploaded on device A
        r = b.post(f"{API}/jobs/create", files={"target_file": ("t.jpg", jpeg((120, 120, 40)), "image/jpeg")},
                   data={"config_json": json.dumps({"mode": "photomosaic", "columns": 8,
                                                     "use_personal_photos": True, "use_community_photos": False})})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["manifest"]["pool"]["personal"], 2)

        # A guest's personal pool is their own empty guest vault, never someone else's
        r = guest.post(f"{API}/jobs/create", files={"target_file": ("t.jpg", jpeg((1, 2, 3)), "image/jpeg")},
                       data={"config_json": json.dumps({"mode": "photomosaic", "use_personal_photos": True,
                                                         "use_community_photos": False})})
        self.assertEqual(r.status_code, 400)  # empty pool

        self.assertEqual(b.delete(f"{API}/library/personal").json()["deleted_photos"], 2)
        self.assertEqual(a.get(f"{API}/library/personal").json()["count"], 0)


class TestPasswordReset(unittest.TestCase):

    def test_forgot_and_reset_password(self):
        name = new_name()
        old = requests.Session()
        signup(old, name)

        # Same answer for unknown emails, so accounts can't be discovered
        unknown = requests.post(f"{API}/auth/forgot-password", json={"email": f"{new_name()}@example.com"}).json()
        self.assertNotIn("reset_link", unknown)

        r = requests.post(f"{API}/auth/forgot-password", json={"email": f"{name}@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["message"], unknown["message"])
        link = r.json()["reset_link"]  # dev stub only
        token = link.split("reset=", 1)[1]

        self.assertEqual(requests.post(f"{API}/auth/reset-password",
                                       json={"token": token + "x", "new_password": "a new password"}).status_code, 400)
        self.assertEqual(requests.post(f"{API}/auth/reset-password",
                                       json={"token": token, "new_password": "short"}).status_code, 400)

        fresh = requests.Session()
        r = fresh.post(f"{API}/auth/reset-password", json={"token": token, "new_password": "a new password"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(fresh.get(f"{API}/auth/me").json()["user"])  # signed in by the reset

        # The link is single-use, old sessions are signed out and the old password is gone
        self.assertEqual(requests.post(f"{API}/auth/reset-password",
                                       json={"token": token, "new_password": "another one!"}).status_code, 400)
        self.assertEqual(old.get(f"{API}/auth/me").json()["user"], None)
        self.assertEqual(login(requests.Session(), name).status_code, 401)
        self.assertEqual(login(requests.Session(), name, "a new password").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
