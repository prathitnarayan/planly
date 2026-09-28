"""Settings loaded from backend/.env (never commit that file)."""

import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Any chat model that supports JSON mode works. Change in .env, not in code.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
# Any OpenAI-compatible provider. Empty = api.openai.com.
# AI Pipe: https://aipipe.org/openai/v1
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL") or None

MAX_INTERVIEW_QUESTIONS = int(os.getenv("MAX_INTERVIEW_QUESTIONS", "6"))

# ---------- Supabase (leave empty for local dev: in-memory data, no login) ----------
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")        # only for scripts/login.py
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")    # only for LEGACY HS256 projects
DATABASE_URL = os.getenv("DATABASE_URL", "")                  # Session pooler connection string

# "supabase" = real login required. "dev" = everyone is one fixed dev user (local only!).
AUTH_MODE = "supabase" if SUPABASE_URL else "dev"
# A real database with no login would let anyone read everyone's plans. Refuse, unless
# you explicitly say this is a throwaway local database.
ALLOW_DEV_AUTH_WITH_DB = os.getenv("ALLOW_DEV_AUTH_WITH_DB", "") == "1"


def check_safe_config() -> None:
    if DATABASE_URL and AUTH_MODE == "dev" and not ALLOW_DEV_AUTH_WITH_DB:
        raise RuntimeError(
            "DATABASE_URL is set but SUPABASE_URL is empty: that would run a real database "
            "with no login. Set SUPABASE_URL (or ALLOW_DEV_AUTH_WITH_DB=1 for a throwaway local DB)."
        )
