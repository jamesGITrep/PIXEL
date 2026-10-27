"""
User accounts for the API Gateway (bucket-only, no SQL).

    bucket-task-coordination/users/{username}.json           profile + bcrypt hash
    bucket-task-coordination/user-emails/{sha256(email)}.json email -> username (uniqueness, login by email)
    bucket-task-coordination/auth/signing-key.json           HMAC key shared by every gateway VM
    bucket-task-coordination/login-failures/{username}/{ms}-{rand}  one object per failed login

Usernames and emails are claimed with create-if-absent writes, so two simultaneous sign-ups can
never get the same name. Sessions are stateless HMAC-signed cookies (HttpOnly, SameSite=Lax), so
any gateway VM behind the internal load balancer can verify them. Each token carries the user's
password version: a password reset bumps it and signs out every existing session.

Failed logins are separate objects rather than a counter in the user record: GCS allows only about
one write per second to a single object, and a burst of bad passwords must not fail (or race).

Forgot password is a stub: there is no mail service yet, so the reset link is written to the
gateway log (Cloud Logging in GCP). AUTH_DEV_RETURN_RESET_LINK=true (local stack only) also returns
it in the API response for testing.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import List, Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from services.common.storage import get_storage, BUCKET_TASK_COORDINATION

logger = logging.getLogger("gateway.auth")
storage = get_storage()

USERS_PREFIX = "users/"
EMAILS_PREFIX = "user-emails/"
SIGNING_KEY_KEY = "auth/signing-key.json"
FAILURES_PREFIX = "login-failures/"

COOKIE_NAME = "pm_session"
SESSION_TTL = 30 * 24 * 3600
RESET_TTL = 30 * 60
MAX_FAILED_LOGINS = 10
LOCKOUT_SECONDS = 15 * 60
BCRYPT_ROUNDS = 12

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
DEV_RETURN_RESET_LINK = os.getenv("AUTH_DEV_RETURN_RESET_LINK", "false").lower() in ("true", "1", "yes")
# Accounts allowed to moderate the community pool (comma-separated usernames; empty = nobody)
ADMIN_USERNAMES = {u.strip().lower() for u in os.getenv("ADMIN_USERNAMES", "").split(",") if u.strip()}

# Compared against when the account does not exist, so response time doesn't reveal usernames
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(BCRYPT_ROUNDS))

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ------------------------------------------------------------------------------
# Storage helpers
# ------------------------------------------------------------------------------

def _now_iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_ts(value: Optional[str]) -> float:
    if not value:
        return 0.0
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _email_key(email: str) -> str:
    return f"{EMAILS_PREFIX}{hashlib.sha256(email.encode('utf-8')).hexdigest()}.json"


def _load_json(key: str) -> Optional[dict]:
    try:
        return json.loads(storage.download_file(BUCKET_TASK_COORDINATION, key, quiet=True))
    except Exception:
        return None


def load_user(username: str) -> Optional[dict]:
    if not USERNAME_RE.match(username or ""):
        return None
    return _load_json(f"{USERS_PREFIX}{username}.json")


def _save_user(user: dict) -> None:
    storage.upload_file(BUCKET_TASK_COORDINATION, f"{USERS_PREFIX}{user['username']}.json",
                        json.dumps(user).encode("utf-8"), "application/json")


def _find_user(identifier: str) -> Optional[dict]:
    identifier = (identifier or "").strip().lower()
    if "@" in identifier:
        entry = _load_json(_email_key(identifier))
        return load_user(entry["username"]) if entry else None
    return load_user(identifier)


def _recent_failures(username: str) -> List[str]:
    """Failed-login markers from the last LOCKOUT_SECONDS (key names start with a ms timestamp)."""
    cutoff_ms = (time.time() - LOCKOUT_SECONDS) * 1000
    recent = []
    for key in storage.list_objects(BUCKET_TASK_COORDINATION, f"{FAILURES_PREFIX}{username}/"):
        stamp = key.rsplit("/", 1)[-1].split("-", 1)[0]
        if stamp.isdigit() and int(stamp) >= cutoff_ms:
            recent.append(key)
    return recent


def _record_failure(username: str) -> int:
    storage.upload_file(BUCKET_TASK_COORDINATION,
                        f"{FAILURES_PREFIX}{username}/{int(time.time() * 1000)}-{secrets.token_hex(4)}", b"1",
                        "text/plain")
    return len(_recent_failures(username))


def _clear_failures(username: str) -> None:
    storage.delete_prefix(BUCKET_TASK_COORDINATION, f"{FAILURES_PREFIX}{username}/")


_signing_key: Optional[bytes] = None


def _key() -> bytes:
    """One random key for the whole deployment, created by whichever gateway asks first."""
    global _signing_key
    if _signing_key is None:
        entry = _load_json(SIGNING_KEY_KEY)
        if entry is None:
            storage.create_if_absent(BUCKET_TASK_COORDINATION, SIGNING_KEY_KEY,
                                     json.dumps({"key": secrets.token_hex(32), "created_at": _now_iso()}).encode())
            entry = _load_json(SIGNING_KEY_KEY)  # re-read: another gateway may have won the race
        _signing_key = bytes.fromhex(entry["key"])
    return _signing_key


# ------------------------------------------------------------------------------
# Session tokens
# ------------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(user: dict) -> str:
    payload = _b64(json.dumps({"usr": user["username"], "uid": user["user_id"],
                               "pv": user.get("password_version", 0),
                               "exp": int(time.time()) + SESSION_TTL}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def current_user(request: Request) -> Optional[dict]:
    """The signed-in user's record, or None. Checks signature, expiry and password version."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if not token or token.count(".") != 1:
        return None
    payload, sig = token.split(".")
    expected = _b64(hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_unb64(payload))
    except Exception:
        return None
    if claims.get("exp", 0) < time.time():
        return None
    user = load_user(claims.get("usr", ""))
    if not user or user["user_id"] != claims.get("uid") or user.get("password_version", 0) != claims.get("pv"):
        return None
    return user


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to use your personal vault")
    return user


# ------------------------------------------------------------------------------
# Guest vaults: visitors who are not signed in still get a private, temporary vault. Its id is a
# random 96-bit value in an HttpOnly *session* cookie (no expiry, so the browser drops it when it
# closes); the photos live under personal/gst_*/ and a bucket lifecycle rule deletes them after a
# day. Signing in moves them into the account (see /api/v1/library/guest/claim).
# ------------------------------------------------------------------------------

GUEST_COOKIE = "pm_guest"
_GUEST_RE = re.compile(r"^gst_[a-f0-9]{24}$")


def guest_id(request: Request) -> Optional[str]:
    gid = request.cookies.get(GUEST_COOKIE, "")
    return gid if _GUEST_RE.match(gid) else None


def clear_guest(response: Response) -> None:
    response.delete_cookie(GUEST_COOKIE, path="/")


def vault_owner(request: Request, response: Optional[Response] = None, create: bool = False):
    """
    (vault_id, is_guest, user) for this request: the account's vault when signed in, otherwise the
    browser session's guest vault. With create=True a guest without one is issued a new id.
    """
    user = current_user(request)
    if user:
        return user["user_id"], False, user
    gid = guest_id(request)
    if not gid and create and response is not None:
        gid = f"gst_{secrets.token_hex(12)}"
        response.set_cookie(GUEST_COOKIE, gid, httponly=True, samesite="lax", path="/",
                            secure=request.headers.get("x-forwarded-proto") == "https")
    return gid, True, None


def _set_session(response: Response, request: Request, user: dict) -> None:
    response.set_cookie(COOKIE_NAME, issue_token(user), max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto") == "https", path="/")


def is_admin(user: Optional[dict]) -> bool:
    return bool(user) and user["username"] in ADMIN_USERNAMES


def require_admin(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with an admin account")
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="This account is not an administrator")
    return user


def public_profile(user: dict) -> dict:
    return {"user_id": user["user_id"], "username": user["username"], "email": user["email"],
            "created_at": user["created_at"], "is_admin": is_admin(user)}


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(password.encode("utf-8")) > 72:  # bcrypt only uses the first 72 bytes
        raise HTTPException(status_code=400, detail="Password must be at most 72 bytes")


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


# ------------------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------------------

class SignupRequest(BaseModel):
    username: str
    email: str
    password: str


class LoginRequest(BaseModel):
    identifier: str  # username or email
    password: str


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str


@router.post("/signup")
def signup(req: SignupRequest, request: Request, response: Response):
    username = req.username.strip().lower()
    email = req.email.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Username must be 3-32 letters, digits or underscores")
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    _check_password(req.password)

    user = {
        "user_id": f"usr_{secrets.token_hex(6)}",
        "username": username,
        "email": email,
        "password_hash": _hash(req.password),
        "created_at": _now_iso(),
        "reset_token": None,
        "reset_token_expiry": None,
        "password_version": 0,
    }
    # Claim the email first, then the username; undo the email claim if the username is taken
    if not storage.create_if_absent(BUCKET_TASK_COORDINATION, _email_key(email),
                                    json.dumps({"username": username}).encode()):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    if not storage.create_if_absent(BUCKET_TASK_COORDINATION, f"{USERS_PREFIX}{username}.json",
                                    json.dumps(user).encode()):
        storage.delete_object(BUCKET_TASK_COORDINATION, _email_key(email))
        raise HTTPException(status_code=409, detail="That username is taken")

    logger.info(f"New account {username} ({user['user_id']})")
    _set_session(response, request, user)
    return {"user": public_profile(user)}


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    user = _find_user(req.identifier)
    failures = _recent_failures(user["username"]) if user else []
    if len(failures) >= MAX_FAILED_LOGINS:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")

    stored = user["password_hash"].encode("ascii") if user else _DUMMY_HASH
    ok = bcrypt.checkpw(req.password.encode("utf-8")[:72], stored) and user is not None
    if not ok:
        if user and _record_failure(user["username"]) >= MAX_FAILED_LOGINS:
            logger.warning(f"Account {user['username']} locked after {MAX_FAILED_LOGINS} failed logins")
        raise HTTPException(status_code=401, detail="Wrong username/email or password")

    if failures:
        _clear_failures(user["username"])
    _set_session(response, request, user)
    return {"user": public_profile(user)}


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def verify_password(user: dict, password: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], user["password_hash"].encode("ascii"))
    except Exception:
        return False


def confirm_owner(user: dict, confirmation: str) -> bool:
    """Destructive account actions need the username typed out, or the password."""
    typed = (confirmation or "").strip()
    if typed.lower() == user["username"]:
        return True
    if not typed:
        return False
    if verify_password(user, typed):
        return True
    _record_failure(user["username"])  # wrong guesses count toward the login lockout
    return False


def delete_account_records(user: dict) -> None:
    """Removes the profile, the email claim and any failed-login markers. Signs out every session,
    because session tokens are only honoured while the user record exists."""
    storage.delete_object(BUCKET_TASK_COORDINATION, f"{USERS_PREFIX}{user['username']}.json")
    storage.delete_object(BUCKET_TASK_COORDINATION, _email_key(user["email"]))
    _clear_failures(user["username"])
    logger.info(f"Deleted account {user['username']} ({user['user_id']})")


@router.post("/logout")
def logout(response: Response):
    clear_session(response)
    return {"status": "signed_out"}


@router.get("/me")
def me(request: Request):
    """The signed-in account, or {"user": null} for guests (not an error: every page load asks)."""
    user = current_user(request)
    return {"user": public_profile(user) if user else None}


@router.post("/forgot-password")
def forgot_password(req: ForgotRequest, request: Request):
    """Same answer whether or not the email exists, so accounts can't be discovered here."""
    email = req.email.strip().lower()
    result = {"status": "ok", "message": "If an account exists for that email, a reset link has been sent."}
    user = _find_user(email) if EMAIL_RE.match(email) else None
    if not user:
        return result

    secret = secrets.token_urlsafe(32)
    user["reset_token"] = hashlib.sha256(secret.encode()).hexdigest()  # only the hash is stored
    user["reset_token_expiry"] = _now_iso(time.time() + RESET_TTL)
    _save_user(user)
    link = f"/?reset={user['username']}.{secret}"
    # Stub delivery: no mail service yet, so the link goes to the gateway log
    logger.info(f"Password reset link for {user['username']} <{email}> (valid 30 min): {link}")
    if DEV_RETURN_RESET_LINK:
        result["reset_link"] = link
    return result


@router.post("/reset-password")
def reset_password(req: ResetRequest, request: Request, response: Response):
    username, _, secret = req.token.partition(".")
    user = load_user(username)
    invalid = HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    if not user or not secret or not user.get("reset_token"):
        raise invalid
    if not hmac.compare_digest(user["reset_token"], hashlib.sha256(secret.encode()).hexdigest()):
        raise invalid
    if _iso_ts(user.get("reset_token_expiry")) < time.time():
        raise invalid
    _check_password(req.new_password)

    user["password_hash"] = _hash(req.new_password)
    user["password_version"] = user.get("password_version", 0) + 1  # signs out every old session
    user["reset_token"] = None
    user["reset_token_expiry"] = None
    _save_user(user)
    _clear_failures(user["username"])  # the owner proved control of the email
    logger.info(f"Password reset for {user['username']}")
    _set_session(response, request, user)
    return {"user": public_profile(user)}
