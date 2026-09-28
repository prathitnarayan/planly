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
