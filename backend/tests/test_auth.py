"""Login: real Supabase-style tokens are accepted, anything forged/expired/mis-addressed is not."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core import config
from app.core.auth import AuthError, verify_token
from app.main import app, get_llm
from tests.test_ai import BLUEPRINT, FakeLLM, turn

URL = "https://abcd1234.supabase.co"
SECRET = "legacy-jwt-secret-for-tests-only-32b"
ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def claims(sub=ALICE, **over):
    base = {"sub": sub, "aud": "authenticated", "iss": f"{URL}/auth/v1",
            "exp": int(time.time()) + 3600, "role": "authenticated"}
    return {**base, **over}


# ---------- new projects: asymmetric keys (ES256) ----------

PRIVATE = ec.generate_private_key(ec.SECP256R1())
OTHER_PRIVATE = ec.generate_private_key(ec.SECP256R1())


class FakeJWKS:
    """Stands in for Supabase's public-key endpoint."""
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=PRIVATE.public_key())


def es256(payload, key=PRIVATE):
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": "test"})


def test_es256_token_accepted():
    assert verify_token(es256(claims()), URL, jwks_client=FakeJWKS()) == ALICE


def test_token_signed_by_someone_else_rejected():
    with pytest.raises(AuthError):
        verify_token(es256(claims(), key=OTHER_PRIVATE), URL, jwks_client=FakeJWKS())


# ---------- legacy projects: shared secret (HS256) ----------

def hs256(payload, secret=SECRET):
    return jwt.encode(payload, secret, algorithm="HS256")


def test_hs256_token_accepted_with_secret():
    assert verify_token(hs256(claims()), URL, jwt_secret=SECRET) == ALICE


def test_hs256_without_configured_secret_rejected():
    with pytest.raises(AuthError, match="unsupported"):
        verify_token(hs256(claims()), URL, jwt_secret="")


@pytest.mark.parametrize("bad", [
    {"exp": int(time.time()) - 10},                 # expired
    {"aud": "anon"},                                # not a signed-in user token
    {"iss": "https://evil.supabase.co/auth/v1"},    # another project's token
])
def test_bad_claims_rejected(bad):
    with pytest.raises(AuthError):
        verify_token(hs256(claims(**bad)), URL, jwt_secret=SECRET)


def test_alg_none_rejected():
    unsigned = jwt.encode(claims(), None, algorithm="none")
    with pytest.raises(AuthError):
        verify_token(unsigned, URL, jwt_secret=SECRET)


def test_garbage_rejected():
    with pytest.raises(AuthError):
        verify_token("not.a.token", URL, jwt_secret=SECRET)


# ---------- through the API ----------

@pytest.fixture
def supabase_mode(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "supabase")
    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", SECRET)


def as_user(user):
    return {"Authorization": f"Bearer {hs256(claims(sub=user))}"}


def test_no_token_is_401(supabase_mode):
    c = TestClient(app)
    assert c.get("/goals").status_code == 401
    assert c.get("/health").status_code == 200          # public


def test_users_cannot_see_each_others_goals(supabase_mode):
    fake = FakeLLM([turn(done=True, deadline="2026-12-01"), BLUEPRINT])
    app.dependency_overrides[get_llm] = lambda: fake
    c = TestClient(app)

    g = c.post("/goals", json={"goal": "Learn SQL"}, headers=as_user(ALICE)).json()
    assert c.get("/me", headers=as_user(ALICE)).json() == {"user_id": ALICE}
    assert len(c.get("/goals", headers=as_user(ALICE)).json()) == 1

    # Bob: can't list it, read it, change it, check in on it, or delete it
    assert c.get("/goals", headers=as_user(BOB)).json() == []
    assert c.get(f"/goals/{g['id']}", headers=as_user(BOB)).status_code == 404
    assert c.post(f"/goals/{g['id']}/blueprint", headers=as_user(BOB)).status_code == 404
    assert c.put(f"/goals/{g['id']}/capacity", json={"slots": []}, headers=as_user(BOB)).status_code == 404
    batch = {"checkins": [{"day": "2026-10-01", "milestone_key": "basics", "outcome": "missed"}]}
    assert c.post(f"/goals/{g['id']}/checkins", json=batch, headers=as_user(BOB)).status_code == 404
    assert c.delete(f"/goals/{g['id']}", headers=as_user(BOB)).status_code == 404

    # ...and Alice's goal is untouched
    assert c.get(f"/goals/{g['id']}", headers=as_user(ALICE)).json()["has_capacity"] is False


def test_unsafe_config_refused(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://x")
    monkeypatch.setattr(config, "AUTH_MODE", "dev")
    monkeypatch.setattr(config, "ALLOW_DEV_AUTH_WITH_DB", False)
    with pytest.raises(RuntimeError, match="no login"):
        config.check_safe_config()


# ---------- remembered sign-in for the terminal scripts ----------

def test_session_refreshes_and_rotates(tmp_path, monkeypatch):
    import json as _json
    import scripts.session as session

    monkeypatch.setattr(session, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(config, "SUPABASE_URL", URL)
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "anon")
    monkeypatch.setattr(config, "SUPABASE_JWT_SECRET", SECRET)
    calls = []

    def fake_auth(path, body):
        calls.append(path)
        if path.startswith("token?grant_type=refresh_token"):
            if body["refresh_token"] == "old-refresh":
                return {"access_token": hs256(claims()), "refresh_token": "new-refresh"}
            raise session.SignInError("Invalid Refresh Token")
        return {"access_token": hs256(claims()), "refresh_token": "fresh-refresh"}

    monkeypatch.setattr(session, "_auth", fake_auth)
    session._save("me@example.com", "old-refresh")

    assert session.current_user() == (ALICE, "me@example.com")          # no password asked
    assert _json.loads(session.SESSION_PATH.read_text())["refresh_token"] == "new-refresh"

    session._save("me@example.com", "revoked")                          # e.g. signed out elsewhere
    monkeypatch.setattr("builtins.input", lambda *_: "me@example.com")
    monkeypatch.setattr("getpass.getpass", lambda *_: "pw")
    assert session.current_user()[0] == ALICE                            # fell back to password
    assert _json.loads(session.SESSION_PATH.read_text())["refresh_token"] == "fresh-refresh"
    assert oct(session.SESSION_PATH.stat().st_mode)[-3:] == "600"        # only you can read it

    session.sign_out()
    assert not session.SESSION_PATH.exists()
    import pytest as _pytest
    with _pytest.raises(session.SignInError):
        session.current_user(interactive=False)
