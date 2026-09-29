"""
Exact YouTube durations with yt-dlp: public data, no login, no API key, no AI.
Used for playlists (Striver's DSA playlists, lecture series) and for videos embedded
in course pages (IITM lectures are YouTube embeds). Results are cached on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

CACHE = Path(__file__).resolve().parents[1] / "data" / "youtube_cache.json"


class _Quiet:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _ydl(flat: bool):
    from yt_dlp import YoutubeDL
    return YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "logger": _Quiet(),
                      "extract_flat": "in_playlist" if flat else False})


def playlist(url: str) -> tuple[str, list[dict]]:
    """(playlist title, [{id, title, seconds}]) for a playlist URL."""
    with _ydl(flat=True) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    videos = [{"id": e.get("id"), "title": e.get("title") or e.get("id"),
               "seconds": e.get("duration")} for e in entries]
    missing = [v["id"] for v in videos if not v["seconds"]]
    if missing:                              # flat mode sometimes omits durations
        found = videos_by_id(missing)
        for v in videos:
            if not v["seconds"] and v["id"] in found:
                v["seconds"] = found[v["id"]]["seconds"]
    return info.get("title") or url, videos


def videos_by_id(ids: list[str]) -> dict[str, dict]:
    """{id: {title, seconds}}, one lookup per new video (cached)."""
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [i for i in dict.fromkeys(ids) if i not in cache]
    if todo:
        with _ydl(flat=False) as ydl:
            for vid in todo:
                try:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
                    cache[vid] = {"title": info.get("title") or vid, "seconds": info.get("duration")}
                except Exception:
                    continue   # private / removed / offline: not cached, retried next sync
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    return {i: cache[i] for i in ids if i in cache}


def clock(seconds: int | None) -> str | None:
    if not seconds:
        return None
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
