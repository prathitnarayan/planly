"""
How much work is left in the user's courses, in study minutes. Pure code, no AI.

Watching a 20-minute lecture properly (pausing, notes, the odd rewind) takes longer
than 20 minutes, so each kind of item gets a factor. These are starting guesses;
later the personal multiplier / ML model replaces them with the user's real pace.
Items we can't size honestly (an assignment with no stated length) are NOT guessed:
they're listed as `unsized` so the blueprint / user decides.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.source import CourseSource, SourceItem

VIDEO_FACTOR = 1.5          # pause, notes, rewind
TEST_FACTOR = 1.5           # attempt + review mistakes
READING_DEFAULT = 15        # minutes, when the page doesn't say
TEST_DEFAULT = 60
PRACTICE_MINUTES = {"easy": 20, "medium": 40, "hard": 60, None: 30}


def study_minutes(item: SourceItem) -> int | None:
    """Minutes of real work for one item, or None if it can't be sized honestly."""
    if item.kind == "video":
        return round(item.minutes * VIDEO_FACTOR) if item.minutes else None
    if item.kind == "live":
        return item.minutes
    if item.kind == "reading":
        return item.minutes or READING_DEFAULT
    if item.kind == "practice":
        return PRACTICE_MINUTES[item.difficulty]
    if item.kind == "test":
        return round((item.minutes or TEST_DEFAULT) * TEST_FACTOR)
    return None   # assignment / other: size depends on the person -> ask, don't guess


class KindLoad(BaseModel):
    kind: str
    items: int
    remaining: int
    shown_minutes: int = 0      # e.g. total video length still to watch
    study_minutes: int = 0      # shown_minutes x factor (+ defaults)


class DueItem(BaseModel):
    title: str
    kind: str
    due: date


class SourceLoad(BaseModel):
    url: str
    title: str
    platform: str
    items: int
    done: int
    study_minutes: int                           # remaining, sized items only
    by_kind: list[KindLoad]
    unsized: list[str] = Field(default_factory=list)   # remaining items we couldn't size
    due: list[DueItem] = Field(default_factory=list)   # remaining items with a date, soonest first

    def describe(self) -> list[str]:
        lines = [f"{self.title} ({self.platform}): {self.done}/{self.items} done, "
                 f"about {self.study_minutes / 60:.1f}h of work left"]
        for k in self.by_kind:
            if not k.remaining:
                continue
            shown = f", {k.shown_minutes / 60:.1f}h shown" if k.shown_minutes else ""
            work = f"{k.study_minutes / 60:.1f}h" if k.study_minutes else "size unknown"
            lines.append(f"  {k.kind}: {k.remaining} left{shown} -> {work}")
        if self.unsized:
            lines.append(f"  not sized (needs your estimate): {len(self.unsized)} items")
        for d in self.due[:5]:
            lines.append(f"  due {d.due:%a %d %b}: {d.title}")
        return lines


def source_load(src: CourseSource, today: date | None = None) -> SourceLoad:
    kinds: dict[str, KindLoad] = {}
    total_study, unsized, due = 0, [], []
    for it in src.items:
        k = kinds.setdefault(it.kind, KindLoad(kind=it.kind, items=0, remaining=0))
        k.items += 1
        if it.done:
            continue
        k.remaining += 1
        mins = study_minutes(it)
        if mins is None:
            unsized.append(it.title)
        else:
            k.study_minutes += mins
            total_study += mins
        if it.minutes and it.kind in ("video", "live", "test"):
            k.shown_minutes += it.minutes
        if it.due and (today is None or it.due >= today):
            due.append(DueItem(title=it.title, kind=it.kind, due=it.due))
    order = ["video", "live", "reading", "practice", "test", "assignment", "other"]
    return SourceLoad(
        url=src.url, title=src.title, platform=src.platform,
        items=len(src.items), done=sum(i.done for i in src.items),
        study_minutes=total_study,
        by_kind=sorted(kinds.values(), key=lambda k: order.index(k.kind)),
        unsized=unsized, due=sorted(due, key=lambda d: d.due),
    )


# ---------- handing the measurement to the blueprint, and checking it was respected ----------

def blueprint_brief(src: CourseSource, today: date | None = None) -> dict:
    """Compact, per-section summary for the blueprint prompt (a 450-problem sheet
    shouldn't be pasted item by item)."""
    load = source_load(src, today)
    sections: dict[str, dict] = {}
    for it in src.items:
        if it.done:
            continue
        s = sections.setdefault(it.section or "(no section)",
                                {"section": it.section or "(no section)", "items": 0,
                                 "study_hours": 0.0, "kinds": {}})
        s["items"] += 1
        s["kinds"][it.kind] = s["kinds"].get(it.kind, 0) + 1
        s["study_hours"] = round(s["study_hours"] + (study_minutes(it) or 0) / 60, 2)
    return {
        "course": src.title, "platform": src.platform,
        "measured_study_hours_left": round(load.study_minutes / 60, 1),
        "done": f"{load.done}/{load.items}",
        "sections_left": list(sections.values()),
        "not_sized": load.unsized[:30],
        "deadlines": [{"title": d.title, "date": d.due.isoformat()} for d in load.due[:30]],
    }


def coverage_warning(plan_hours: float, sources: list[CourseSource], today: date | None = None) -> str | None:
    """The AI is told to cover all measured course work; code checks it did."""
    measured = sum(source_load(s, today).study_minutes for s in sources) / 60
    if measured and plan_hours < measured * 0.9:
        return (f"Your courses measure about {measured:.0f}h of work left, but the plan only has "
                f"{plan_hours:.0f}h. Regenerate the plan, or raise the hours.")
    return None


def course_key_dates(src: CourseSource, today: date | None = None) -> list["KeyDate"]:
    """Graded work with a date on the course page becomes a HARD key date, so the planner
    treats it like any deadline the user typed in. Only future ones."""
    import re

    from app.schemas.interview import KeyDate

    out, seen = [], set()
    for it in src.items:
        if it.done or not it.due or it.kind not in ("assignment", "test"):
            continue
        if today and it.due < today:
            continue
        name = f"{it.section or ''} {it.title}".strip()
        key = "src_" + (re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "item")
        if key in seen:
            key = f"{key}_{it.due:%m%d}"
        seen.add(key)
        out.append(KeyDate(key=key, label=f"{it.title} — {src.title}", date=it.due, hard=True))
    return out
