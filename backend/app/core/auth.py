"""
Who is calling? Every request carries the Supabase login token (a JWT) in the
Authorization header. We verify it here and return the user's id.

Supabase projects sign tokens one of two ways:
  - NEW: asymmetric keys (ES256 / RS256). We fetch Supabase's PUBLIC keys from
    {SUPABASE_URL}/auth/v1/.well-known/jwks.json — nothing secret on our side.
  - LEGACY: one shared secret (HS256) -> set SUPABASE_JWT_SECRET.
Only these algorithms are accepted — never "none", never whatever the token asks for.

Local dev (no SUPABASE_URL): no login, everyone is DEV_USER_ID.
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core import config

DEV_USER_ID = "00000000-0000-0000-0000-000000000001"
ASYMMETRIC = {"ES256", "RS256"}

bearer = HTTPBearer(auto_error=False)   # also adds the "Authorize" button in /docs


class AuthError(Exception):
    pass


@lru_cache
def _jwks_client(supabase_url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def verify_token(
    token: str,
    supabase_url: str,
    jwt_secret: str = "",
    jwks_client: jwt.PyJWKClient | None = None,
) -> str:
    """Return the user id (the token's `sub`) or raise AuthError."""
    try:
        alg = jwt.get_unverified_header(token).get("alg")
        if alg in ASYMMETRIC:
            client = jwks_client or _jwks_client(supabase_url)
            key = client.get_signing_key_from_jwt(token).key
        elif alg == "HS256" and jwt_secret:
            key = jwt_secret
        else:
            raise AuthError(f"unsupported token algorithm: {alg}")
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience="authenticated",
            issuer=f"{supabase_url}/auth/v1",
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except AuthError:
        raise
    except (jwt.PyJWTError, ValueError, KeyError) as e:
        raise AuthError(str(e)) from e
    return claims["sub"]


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if config.AUTH_MODE == "dev":
        return DEV_USER_ID
    if creds is None:
        raise HTTPException(status_code=401, detail="sign in first (Authorization: Bearer <token>)")
    try:
        return verify_token(creds.credentials, config.SUPABASE_URL, config.SUPABASE_JWT_SECRET)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=f"invalid token: {e}")
