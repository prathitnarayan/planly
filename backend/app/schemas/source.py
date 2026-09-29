"""
Study material the user has to get through: a course, a playlist, a problem sheet.

Filled by the sync tool (backend/sync) from the real course page. The AI only READS
the page and lists what's on it, copying durations as written ("12:34", "1h 5m").
Code turns those into minutes (parse_duration) and adds them up (planners/load.py).
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Kind = Literal["video", "reading", "practice", "assignment", "test", "live", "other"]
Difficulty = Literal["easy", "medium", "hard"]


# ---------- durations: the model copies text, code does the maths ----------

_UNITS = {
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
}


def parse_duration(text: str | None) -> int | None:
    """'12:34' -> 13, '1:02:03' -> 63, '1h 5m' -> 65, 'PT4M13S' -> 5, '1.5 hours' -> 90.
    Minutes, rounded UP (a 12:01 video still eats 13 minutes). None if there's no duration."""
    if not text:
        return None
    t = text.strip().lower()

    iso = re.fullmatch(r"pt(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if iso and any(iso.groups()):
        h, m, s = (int(g or 0) for g in iso.groups())
        return _ceil_min(h * 3600 + m * 60 + s)

    clock = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", t)
    if clock:
        a, b, c = clock.groups()
        secs = int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)
        return _ceil_min(secs)

    total, found = 0.0, False
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", t):
        if unit in _UNITS:
            total += float(num) * _UNITS[unit]
            found = True
    return _ceil_min(total) if found and total > 0 else None


def _ceil_min(seconds: float) -> int | None:
    return max(1, math.ceil(seconds / 60)) if seconds > 0 else None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_due(text: str | None, today: date | None = None) -> date | None:
    """Due date as written on the page -> a real date. Code picks the year, never the AI:
    no year written -> the occurrence nearest to today (a course page lists this term's dates).
    Numeric dates are read day-first (15/10 = 15 Oct), as Indian portals write them."""
    if not text:
        return None
    t = text.strip().lower()
    today = today or date.today()

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return _safe(int(m[1]), int(m[2]), int(m[3]))

    day = month = year = None
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s*(\d{4})?", t)      # 15 Oct 2026
    if m and m[2] in _MONTHS:
        day, month, year = int(m[1]), _MONTHS[m[2]], m[3]
    else:
        m = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", t)   # Oct 15, 2026
        if m and m[1] in _MONTHS:
            day, month, year = int(m[2]), _MONTHS[m[1]], m[3]
        else:
            m = re.search(r"\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)            # 15/10/2026
            if m:
                day, month, year = int(m[1]), int(m[2]), m[3]
    if day is None:
        return None
    if year:
        y = int(year)
        return _safe(y + 2000 if y < 100 else y, month, day)
    options = [d for d in (_safe(today.year + k, month, day) for k in (-1, 0, 1)) if d]
    return min(options, key=lambda d: abs((d - today).days)) if options else None


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ---------- what the AI returns for one page ----------

class ExtractedItem(BaseModel):
    title: str
    section: str | None = None          # e.g. "Week 3" / "Arrays"
    kind: Kind = "other"
    duration_text: str | None = None    # exactly as written on the page
    due_text: str | None = None         # exactly as written; code turns it into a date
    difficulty: Difficulty | None = None
    done: bool = False                  # page shows it as completed / solved / watched
    url: str | None = None

    @field_validator("difficulty", mode="before")
    @classmethod
    def normalise_difficulty(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            return v if v in ("easy", "medium", "hard") else None
        return v

    @field_validator("kind", mode="before")
    @classmethod
    def normalise_kind(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            aliases = {"lecture": "video", "problem": "practice", "question": "practice",
                       "quiz": "test", "exam": "test", "mock": "test", "article": "reading",
                       "notes": "reading", "pdf": "reading", "project": "assignment"}
            v = aliases.get(v, v)
            return v if v in ("video", "reading", "practice", "assignment", "test", "live", "other") else "other"
        return v


class ExtractedPage(BaseModel):
    course_title: str | None = None
    items: list[ExtractedItem] = Field(default_factory=list)


# ---------- stored ----------

class SourceItem(BaseModel):
    title: str
    section: str | None = None
    kind: Kind = "other"
    minutes: int | None = None          # length as shown on the page (video / test duration)
    duration_text: str | None = None
    due: date | None = None
    due_text: str | None = None
    difficulty: Difficulty | None = None
    done: bool = False
    url: str | None = None

    @classmethod
    def from_extracted(cls, e: ExtractedItem, today: date | None = None) -> "SourceItem":
        return cls(**e.model_dump(), minutes=parse_duration(e.duration_text),
                   due=parse_due(e.due_text, today))


class CourseSource(BaseModel):
    url: str
    platform: str
    title: str
    items: list[SourceItem] = Field(default_factory=list)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
