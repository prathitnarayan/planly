"""
Sign up / sign in to your Supabase project and print an access token.

    python -m scripts.login          # sign in (or sign up if you're new)

Paste the token into /docs -> "Authorize". It lasts about an hour; run this again after.
Uses SUPABASE_URL + SUPABASE_ANON_KEY from backend/.env (the anon/publishable key is
meant to be public — it only lets people sign in; RLS protects the data).
"""

from __future__ import annotations

import getpass
import sys

import httpx

from app.core import config
from app.core.auth import AuthError, verify_token


def auth_call(path: str, body: dict) -> httpx.Response:
    return httpx.post(
        f"{config.SUPABASE_URL}/auth/v1/{path}",
        json=body,
        headers={"apikey": config.SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        timeout=20,
    )


def main() -> None:
    if not config.SUPABASE_URL or not config.SUPABASE_ANON_KEY:
        sys.exit("Set SUPABASE_URL and SUPABASE_ANON_KEY in backend/.env first.")

    email = input("Email: ").strip()
    password = getpass.getpass("Password (min 6 chars, hidden): ")

    res = auth_call("token?grant_type=password", {"email": email, "password": password})
    if res.status_code == 400 and input("No account with that email/password. Create one? [y/N]: ").lower() == "y":
        res = auth_call("signup", {"email": email, "password": password})
        if res.status_code < 300 and "access_token" not in res.json():
            print("\nAccount created — check your email and click the confirmation link, then run this again.")
            print("(For local testing you can turn off 'Confirm email' in Supabase: Authentication -> Sign In / Providers -> Email.)")
            return
    if res.status_code >= 300:
        sys.exit(f"Sign-in failed ({res.status_code}): {res.json().get('msg') or res.json().get('error_description') or res.text}")

    token = res.json()["access_token"]
    try:
        user_id = verify_token(token, config.SUPABASE_URL, config.SUPABASE_JWT_SECRET)
    except AuthError as e:
        sys.exit(f"Signed in, but the backend can't verify this token: {e}\n"
                 "If your project uses the legacy JWT secret, set SUPABASE_JWT_SECRET in .env.")
    print(f"\n✅ Signed in as {email} (user id {user_id}). The backend verified the token.\n")
    print("Access token (paste into /docs -> Authorize):\n")
    print(token)


if __name__ == "__main__":
    main()
