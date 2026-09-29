"""
Sites the sync tool knows. Every site is read the same generic way (open the page in
your logged-in browser, expand everything, take the text, let the AI list the items),
so a new site needs only a line here, not a scraper. YouTube playlists are special:
yt-dlp gives exact durations with no login and no AI.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Platform:
    name: str
    label: str
    domains: tuple[str, ...]
    login_url: str | None       # None = public, no login needed
    reader: str = "page"        # "page" | "youtube"


PLATFORMS: list[Platform] = [
    Platform("iitm", "IITM BS (seek)", ("onlinedegree.iitm.ac.in", "iitm.ac.in"),
             "https://seek.onlinedegree.iitm.ac.in/"),
    Platform("youtube", "YouTube", ("youtube.com", "youtu.be"), None, reader="youtube"),
    Platform("striver", "Striver / takeUforward", ("takeuforward.org",),
             "https://takeuforward.org/plus/login"),
    Platform("coursera", "Coursera", ("coursera.org",), "https://www.coursera.org/?authMode=login"),
    Platform("udemy", "Udemy", ("udemy.com",), "https://www.udemy.com/join/passwordless-auth/"),
    Platform("leetcode", "LeetCode", ("leetcode.com",), "https://leetcode.com/accounts/login/"),
    Platform("codeforces", "Codeforces", ("codeforces.com",), "https://codeforces.com/enter"),
    Platform("codechef", "CodeChef", ("codechef.com",), "https://www.codechef.com/login"),
    Platform("gfg", "GeeksforGeeks", ("geeksforgeeks.org",), "https://www.geeksforgeeks.org/"),
    Platform("rankers", "Rankers Gurukul", ("rankersgurukul.com",), "https://rankersgurukul.com/"),
    Platform("parmar", "Parmar Academy", ("parmaracademy.in",), "https://www.parmaracademy.in/"),
    Platform("testbook", "Testbook", ("testbook.com",), "https://testbook.com/login"),
]

BY_NAME = {p.name: p for p in PLATFORMS}


def detect(url: str) -> Platform:
    """Known site from the URL, or a generic one named after the domain."""
    host = (urlparse(url).hostname or "").lower()
    for p in PLATFORMS:
        if any(host == d or host.endswith("." + d) for d in p.domains):
            return p
    base = host.removeprefix("www.").split(".")[0] or "web"
    return Platform(base, host or url, (host,), f"https://{host}/" if host else None)


def is_youtube_playlist(url: str) -> bool:
    return "list=" in url and detect(url).name == "youtube"
