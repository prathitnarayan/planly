"""
Google Calendar, READ-ONLY free/busy.

Scope: calendar.freebusy ("see when you're busy"). Planly never sees event titles, attendees
or descriptions, and never creates events, so your calendar sends no extra notifications.
Busy times are cached for 30 minutes; the refresh token is stored encrypted with APP_SECRET.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt

from app.core import config

SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
REVOKE = "https://oauth2.googleapis.com/revoke"
FREEBUSY = "https://www.googleapis.com/calendar/v3/freeBusy"
CACHE = timedelta(minutes=30)
HORIZON = timedelta(days=90)


class CalendarError(Exception):
    pass


def available() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET and config.APP_SECRET and config.PUBLIC_API_URL)


def redirect_uri() -> str:
    return f"{config.PUBLIC_API_URL}/google/callback"


# ---------- tokens at rest ----------

def _fernet():
    from cryptography.fernet import Fernet
    if not config.APP_SECRET:
        raise CalendarError("APP_SECRET is not set on the backend.")
    key = base64.urlsafe_b64encode(hashlib.sha256(("planly-google:" + config.APP_SECRET).encode()).digest())
    return Fernet(key)


def encrypt(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt(blob: str) -> str:
    return _fernet().decrypt(blob.encode()).decode()


# ---------- OAuth ----------

def auth_url(user_id: str) -> str:
    state = jwt.encode({"sub": user_id, "purpose": "google",
                        "exp": datetime.now(timezone.utc) + timedelta(minutes=10)}, config.APP_SECRET, "HS256")
    return AUTH + "?" + urlencode({
        "client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
    })


def user_from_state(state: str) -> str:
    try:
        data = jwt.decode(state, config.APP_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise CalendarError(f"sign-in link expired or invalid ({e.__class__.__name__})")
    if data.get("purpose") != "google":
        raise CalendarError("wrong state")
    return data["sub"]


def _post(url: str, data: dict) -> dict:
    try:
        r = httpx.post(url, data=data, timeout=20)
    except httpx.HTTPError as e:
        raise CalendarError(f"Google unreachable ({e.__class__.__name__})")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        raise CalendarError(body.get("error_description") or body.get("error") or f"Google said {r.status_code}")
    return body


def exchange(code: str) -> str:
    """Authorization code -> refresh token."""
    body = _post(TOKEN, {"code": code, "client_id": config.GOOGLE_CLIENT_ID,
                         "client_secret": config.GOOGLE_CLIENT_SECRET,
                         "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
    if not body.get("refresh_token"):
        raise CalendarError("Google didn't return a refresh token; remove Planly's access in your Google account and connect again.")
    return body["refresh_token"]


def access_token(refresh: str) -> str:
    return _post(TOKEN, {"refresh_token": refresh, "client_id": config.GOOGLE_CLIENT_ID,
                         "client_secret": config.GOOGLE_CLIENT_SECRET, "grant_type": "refresh_token"})["access_token"]


def revoke(refresh: str) -> None:
    try:
        httpx.post(REVOKE, params={"token": refresh}, timeout=10)
    except httpx.HTTPError:
        pass


def freebusy(refresh: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Busy intervals (UTC) on the primary calendar. Only times, nothing else."""
    token = access_token(refresh)
    try:
        r = httpx.post(FREEBUSY, headers={"Authorization": f"Bearer {token}"}, timeout=20, json={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": "primary"}]})
    except httpx.HTTPError as e:
        raise CalendarError(f"Google unreachable ({e.__class__.__name__})")
    if r.status_code >= 400:
        raise CalendarError(f"Google Calendar said {r.status_code}")
    busy = r.json().get("calendars", {}).get("primary", {}).get("busy", [])
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    return [(parse(b["start"]), parse(b["end"])) for b in busy]
