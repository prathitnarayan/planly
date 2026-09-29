"""
Course page text -> list of study items.

The page text can be long (a full IITM term, a 450-problem sheet), so it's cut into
chunks at line breaks; each chunk is read separately and the results are merged.
The AI only copies what's written; parse_duration / parse_due / planners.load do the maths.
"""

from __future__ import annotations

import json
from datetime import date

from app.ai.llm import LLMClient, generate_validated
from app.ai.prompts import SOURCE_EXTRACT_SYSTEM
from app.schemas.source import CourseSource, ExtractedItem, ExtractedPage, SourceItem

CHUNK_CHARS = 12_000
MAX_CHARS = 400_000


def chunk_text(text: str, size: int | None = None) -> list[str]:
    """Split at line breaks so no item is cut in half (a single huge line is hard-split)."""
    size = size or CHUNK_CHARS
    chunks, cur, cur_len = [], [], 0
    for line in text.splitlines():
        while len(line) > size:
            if cur:
                chunks.append("\n".join(cur))
                cur, cur_len = [], 0
            chunks.append(line[:size])
            line = line[size:]
        if cur_len + len(line) + 1 > size and cur:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += len(line) + 1
    if cur and any(s.strip() for s in cur):
        chunks.append("\n".join(cur))
    return chunks


def _key(it: ExtractedItem) -> tuple[str, str]:
    return ((it.section or "").strip().lower(), " ".join(it.title.lower().split()))


def merge_items(items: list[ExtractedItem]) -> list[ExtractedItem]:
    """Same item seen twice (chunk overlap, or listed in a sidebar and the main page):
    keep one, fill blanks from the other, and done if either says done."""
    merged: dict[tuple[str, str], ExtractedItem] = {}
    for it in items:
        k = _key(it)
        if k not in merged:
            merged[k] = it.model_copy()
            continue
        cur = merged[k]
        for field in ("duration_text", "due_text", "difficulty", "url"):
            if getattr(cur, field) is None and getattr(it, field) is not None:
                setattr(cur, field, getattr(it, field))
        if cur.kind == "other" and it.kind != "other":
            cur.kind = it.kind
        cur.done = cur.done or it.done
    return list(merged.values())


def extract_source(
    llm: LLMClient, *, url: str, platform: str, text: str,
    title: str | None = None, today: date | None = None,
) -> CourseSource:
    text = text[:MAX_CHARS]
    found: list[ExtractedItem] = []
    course_title = title
    section: str | None = None
    for i, chunk in enumerate(chunk_text(text)):
        brief = {
            "platform": platform, "url": url, "chunk": f"{i + 1}",
            "previous_section": section,
            "page_text": chunk,
        }
        page = generate_validated(
            llm, ExtractedPage, SOURCE_EXTRACT_SYSTEM,
            [{"role": "user", "content": "List the study items as JSON:\n" + json.dumps(brief)}],
        )
        course_title = course_title or page.course_title
        found += page.items
        if page.items and page.items[-1].section:
            section = page.items[-1].section
    items = [SourceItem.from_extracted(e, today) for e in merge_items(found)]
    return CourseSource(url=url, platform=platform, title=course_title or url, items=items)
