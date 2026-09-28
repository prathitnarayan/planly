"""
Remembered Supabase sign-in for the terminal scripts.

Signs in once, then keeps the REFRESH token in backend/data/session.json (gitignored)
and trades it for a fresh access token each run. Refresh tokens rotate: every refresh
returns a new one, which we save. `sign_out()` deletes the file.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import httpx

from app.core import config
from app.core.auth import verify_token

SESSION_PATH = Path(__file__).resolve().parents[1] / "data" / "session.json"


class SignInError(Exception):
    pass


def _auth(path: str, body: dict) -> dict:
    res = httpx.post(
        f"{config.SUPABASE_URL}/auth/v1/{path}",
        json=body,
        headers={"apikey": config.SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        timeout=20,
    )
    if res.status_code >= 300:
        try:
            data = res.json()
            msg = data.get("msg") or data.get("error_description") or res.text
        except ValueError:
            msg = res.text
        raise SignInError(msg)
    return res.json()


def _save(email: str, refresh_token: str) -> None:
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps({"email": email, "refresh_token": refresh_token}))
    SESSION_PATH.chmod(0o600)   # only you can read it


def sign_out() -> None:
    SESSION_PATH.unlink(missing_ok=True)


def current_user(interactive: bool = True) -> tuple[str, str]:
    """
    Return (user_id, email). Uses the saved session if it still works;
    otherwise asks for email + password (once) and saves the new session.
    """
    if not (config.SUPABASE_URL and config.SUPABASE_ANON_KEY):
        raise SignInError("Set SUPABASE_URL and SUPABASE_ANON_KEY in backend/.env first.")

    if SESSION_PATH.exists():
        saved = json.loads(SESSION_PATH.read_text())
        try:
            data = _auth("token?grant_type=refresh_token", {"refresh_token": saved["refresh_token"]})
            _save(saved["email"], data["refresh_token"])
            return verify_token(data["access_token"], config.SUPABASE_URL, config.SUPABASE_JWT_SECRET), saved["email"]
        except (SignInError, KeyError):
            sign_out()   # expired or revoked: fall through to a fresh sign-in

    if not interactive:
        raise SignInError("Not signed in.")
    email = input("Email: ").strip()
    password = getpass.getpass("Password (hidden): ")
    data = _auth("token?grant_type=password", {"email": email, "password": password})
    _save(email, data["refresh_token"])
    return verify_token(data["access_token"], config.SUPABASE_URL, config.SUPABASE_JWT_SECRET), email
