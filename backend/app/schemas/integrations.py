"""Per-user integrations: notifications (Telegram) and Google Calendar free/busy (007)."""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, Field


class NotifyPrefs(BaseModel):
    """At most two messages a day, both optional."""
    morning: bool = True
    morning_at: time = time(8, 0)
    evening: bool = True
    evening_at: time = time(21, 30)
    last_morning: date | None = None
    last_evening: date | None = None


class TelegramLink(BaseModel):
    chat_id: int | None = None
    username: str | None = None
    link_code: str | None = None               # one-time code for t.me/<bot>?start=<code>
    link_expires: datetime | None = None
    # short tokens behind the tick buttons in the last messages (Telegram allows 64-byte button data)
    buttons: dict[str, dict] = Field(default_factory=dict)


class GoogleLink(BaseModel):
    """READ-ONLY free/busy. Planly never sees event titles and never writes to the calendar."""
    refresh_token_enc: str | None = None       # encrypted with APP_SECRET
    connected_at: datetime | None = None
    busy: list[tuple[datetime, datetime]] = Field(default_factory=list)   # UTC
    busy_from: datetime | None = None
    busy_until: datetime | None = None
    fetched_at: datetime | None = None
    error: str | None = None

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token_enc)
