"""
Reading things the SERVER is allowed to read on its own: public data only.

  YouTube   official Data API v3 (YOUTUBE_API_KEY). Exact durations for videos and
            playlists. (yt-dlp from a cloud server gets blocked as a bot; the API doesn't.)
  Public    a page anyone can open without logging in (a course's public curriculum).
            Plain HTTP fetch -> text. Pages that need a login or build themselves with
            JavaScript come back thin: that's what the Chrome extension is for.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import httpx

from app.core import config

YT = "https://www.googleapis.com/youtube/v3"


class NotAvailable(Exception):
    """Can't read this from the server (explained in the message)."""


# ---------- YouTube ----------

def _yt(path: str, **params) -> dict:
    if not config.YOUTUBE_API_KEY:
        raise NotAvailable("YouTube needs YOUTUBE_API_KEY on the backend (free key from Google Cloud "
                           "Console -> APIs -> YouTube Data API v3 -> Credentials).")
    r = httpx.get(f"{YT}/{path}", params={**params, "key": config.YOUTUBE_API_KEY}, timeout=20)
    if r.status_code != 200:
        raise NotAvailable(f"YouTube API said {r.status_code}: {r.text[:200]}")
    return r.json()


def youtube_videos(ids: list[str]) -> dict[str, dict]:
    """{id: {"title", "duration"}} with duration as ISO text ('PT12M34S'), 50 per call."""
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(i for i in ids if re.fullmatch(r"[A-Za-z0-9_-]{11}", i or "")))
    for i in range(0, len(ids), 50):
        data = _yt("videos", part="contentDetails,snippet", id=",".join(ids[i:i + 50]), maxResults=50)
        for v in data.get("items", []):
            out[v["id"]] = {"title": v["snippet"]["title"], "duration": v["contentDetails"]["duration"]}
    return out


def playlist_id(url: str) -> str | None:
    q = parse_qs(urlparse(url).query)
    return (q.get("list") or [None])[0]


def youtube_playlist(url: str, max_items: int = 1000) -> tuple[str, list[dict]]:
    """(title, [{"id", "title", "duration"}]) in playlist order."""
    pid = playlist_id(url)
    if not pid:
        raise NotAvailable("That YouTube link isn't a playlist (it has no list=...).")
    meta = _yt("playlists", part="snippet", id=pid).get("items") or []
    if not meta:
        raise NotAvailable("Playlist not found (private, or a typo in the link).")
    ids, token = [], None
    while len(ids) < max_items:
        page = _yt("playlistItems", part="contentDetails", playlistId=pid, maxResults=50,
                   **({"pageToken": token} if token else {}))
        ids += [it["contentDetails"]["videoId"] for it in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    vids = youtube_videos(ids)
    return meta[0]["snippet"]["title"], [{"id": i, **vids[i]} for i in ids if i in vids]


# ---------- public pages ----------

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "br", "ul", "ol", "table", "details", "summary", "a", "span"}

    def __init__(self):
        super().__init__()
        self.parts, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _Text()
    p.feed(html)
    lines = (" ".join(l.split()) for l in "".join(p.parts).splitlines())
    return " ".join(p.title.split()), "\n".join(l for l in lines if l)


def _check_public_host(url: str) -> None:
    """The server fetches links users paste, so never let one point at the server's own
    network (localhost, 10.x, cloud metadata at 169.254.x...)."""
    import ipaddress
    import socket

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise NotAvailable("Only http(s) links.")
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, u.port or 443)}
    except socket.gaierror:
        raise NotAvailable("That site's address doesn't exist.")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global:
            raise NotAvailable("That link points to a private address.")


def _get(url: str, max_redirects: int = 5) -> httpx.Response:
    for _ in range(max_redirects + 1):
        _check_public_host(url)
        r = httpx.get(url, follow_redirects=False, timeout=25,
                      headers={"User-Agent": "Mozilla/5.0 (Planly course reader)"})
        if r.is_redirect and "location" in r.headers:
            url = str(r.url.join(r.headers["location"]))
            continue
        return r
    raise NotAvailable("Too many redirects.")


def public_page(url: str) -> tuple[str, str]:
    """(title, text) of a public page."""
    try:
        r = _get(url)
    except httpx.HTTPError as e:
        raise NotAvailable(f"Couldn't open that page ({e.__class__.__name__}).")
    if r.status_code in (401, 403):
        raise NotAvailable("That page needs a login. Use the Planly Chrome extension on it instead.")
    if r.status_code >= 400:
        raise NotAvailable(f"That page returned {r.status_code}.")
    title, text = html_to_text(r.text[:3_000_000])
    if len(text) < 200:
        raise NotAvailable("That page came back almost empty (it probably needs a login or builds "
                           "itself in the browser). Use the Planly Chrome extension on it instead.")
    return title, text
