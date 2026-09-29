"""
Concrete daily tasks: which lectures / problems each planned session covers.

The plan says "Basic DSA · learn · 90 min". The user's courses say what the lectures are.
This joins them, in code, with no AI: every session pulls the next unfinished items of a
matching kind from the goal's courses, in course order, until its minutes are used up.

  learn    -> videos, live classes, readings
  practice -> problems
  assess   -> tests / mocks
  project  -> assignments

A long video can be split across sessions ("G-12, from 40% to 100%"); a leftover under
5 minutes is folded into the session instead of becoming its own fragment.
Items already done (the site shows them done, or Planly credited them) are skipped.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel

from app.planners.load import study_minutes
from app.schemas.source import CourseSource

KIND_MAP: dict[str, tuple[str, ...]] = {
    "learn": ("video", "live", "reading"),
    "practice": ("practice",),
    "assess": ("test",),
    "project": ("assignment",),
}
MIN_FRAGMENT = 5
YOUTUBE_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})")


class SessionItem(BaseModel):
    key: str                      # stable id of the course item: "<source hash>:<index>"
    title: str
    kind: str
    url: str | None = None
    video_key: str | None = None  # "yt:<id>" — what the extension reports watch time against
    minutes: int                  # study minutes of this item planned in this session
    part_from: float = 0.0        # which part of the item (videos: of its length)
    part_to: float = 1.0
    length_minutes: int | None = None   # the item's shown length (video minutes)
    verify: Literal["video", "site", "none"] = "none"


def source_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:8]


def item_key(src: CourseSource, index: int) -> str:
    return f"{source_hash(src.url)}:{index}"


def video_key(url: str | None, kind: str = "video") -> str | None:
    """What the extension reports watch time against. YouTube: "yt:<id>". A lecture with its
    own page (Udemy / Coursera / IITM lecture URL): "page:<origin><path>#1" (first video on it)."""
    m = YOUTUBE_ID.search(url or "")
    if m:
        return f"yt:{m.group(1)}"
    if kind in ("video", "live") and url and re.match(r"^https?://", url):
        from urllib.parse import urlsplit
        u = urlsplit(url)
        return f"page:{u.scheme}://{u.netloc}{u.path or '/'}#1"
    return None


def _verify_kind(src: CourseSource, url: str | None, kind: str = "video") -> str:
    if video_key(url, kind):
        return "video"
    # pages read with the user's login show ticks / "solved" / "completed" on re-sync
    return "none" if src.platform == "youtube" else "site"


def attach_items(
    sessions: list, sources: list[CourseSource], done_keys: set[str],
    part_done: dict[str, float] | None = None,
) -> list[list[SessionItem]]:
    """For each session (chronological), the course items it covers. `sessions` need
    .kind and .minutes. `part_done`: items already credited up to a fraction (a long video
    whose first half was done yesterday continues from 50%). One list per session."""
    part_done = part_done or {}
    queues: dict[str, list[tuple[CourseSource, int]]] = {g: [] for g in KIND_MAP}
    for src in sources:
        for i, it in enumerate(src.items):
            if it.done or item_key(src, i) in done_keys or study_minutes(it) is None:
                continue
            for group, kinds in KIND_MAP.items():
                if it.kind in kinds:
                    queues[group].append((src, i))
    left: dict[str, float] = {}   # item key -> study minutes not yet assigned

    out: list[list[SessionItem]] = []
    for s in sessions:
        kind = getattr(s.kind, "value", s.kind)
        q = queues.get(kind)
        got: list[SessionItem] = []
        fill = float(s.minutes)
        while q and fill > 0:
            src, i = q[0]
            it = src.items[i]
            key = item_key(src, i)
            total = float(study_minutes(it) or 0)
            rem = left.get(key, total * (1 - min(0.999, part_done.get(key, 0.0))))
            take = min(fill, rem)
            if rem - take < MIN_FRAGMENT:        # don't leave a 3-minute scrap for tomorrow
                take = rem
            done_before = total - rem
            got.append(SessionItem(
                key=key, title=it.title, kind=it.kind, url=it.url, video_key=video_key(it.url, it.kind),
                minutes=max(1, round(take)),
                part_from=round(done_before / total, 3) if total else 0.0,
                part_to=round((done_before + take) / total, 3) if total else 1.0,
                length_minutes=it.minutes, verify=_verify_kind(src, it.url, it.kind),
            ))
            left[key] = rem - take
            fill -= take
            if left[key] <= 0:
                q.pop(0)
        out.append(got)
    return out
