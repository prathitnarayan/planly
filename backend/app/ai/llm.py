"""
LLM layer. Everything else talks to `LLMClient`, never to OpenAI directly,
so switching provider = writing one new class here.

Pattern used everywhere:
  1. ask the model for JSON
  2. validate with a Pydantic model
  3. if invalid, send the error back and ask it to fix (1 retry)
  4. still invalid -> raise LLMOutputError (the API returns a clean error)
"""

from __future__ import annotations

import json
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

Message = dict[str, str]  # {"role": "user" | "assistant", "content": "..."}


class LLMOutputError(Exception):
    """The model couldn't produce valid output even after retrying."""


class LLMClient(Protocol):
    def complete_json(self, system: str, messages: list[Message]) -> str:
        """Return the model's raw text reply (expected to be a JSON object)."""
        ...


class OpenAIClient:
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import OpenAI  # imported here so tests don't need a key

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing — add it to backend/.env")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete_json(self, system: str, messages: list[Message]) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *messages],
            response_format={"type": "json_object"},  # prompt must mention JSON
        )
        return resp.choices[0].message.content or ""


def generate_validated(
    llm: LLMClient,
    model_cls: type[T],
    system: str,
    messages: list[Message],
    retries: int = 1,
    context: dict | None = None,
) -> T:
    convo = list(messages)
    last_error = ""
    for _ in range(retries + 1):
        raw = llm.complete_json(system, convo)
        try:
            return model_cls.model_validate(json.loads(raw), context=context)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)
            convo += [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "Your JSON was invalid:\n"
                    f"{last_error}\n"
                    "Return the corrected JSON object only.",
                },
            ]
    raise LLMOutputError(f"invalid {model_cls.__name__} after retry: {last_error}")
