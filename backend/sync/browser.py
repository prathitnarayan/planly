"""
A real browser on YOUR laptop, with a saved login.

`login` opens a visible browser; you sign in by hand (Google, OTP, 2FA all fine) and
close it. The cookies stay in backend/data/browser/ (gitignored, only on this laptop).
Your passwords are never seen, typed or stored by Planly.

`read_course` reopens that same profile (hidden), opens the course page, expands every
week / module, scrolls lazy lists, and returns the visible text plus any embedded
YouTube videos. It only READS what the page shows you. No downloads, no DRM, no
CAPTCHA solving: if a site challenges the bot, it shows you the window instead.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

PROFILE_DIR = Path(__file__).resolve().parents[1] / "data" / "browser" / "profile"

LOGIN_HINTS = re.compile(r"/(login|signin|sign-in|sign_in|enter|auth|accounts/login)\b", re.I)
CHALLENGE_HINTS = re.compile(r"verify you are human|just a moment|checking your browser|captcha", re.I)
EXPAND_TEXT = re.compile(r"^\s*(expand all|expand|show more|load more|view more|see more|see all|"
                         r"show all|view all|more lessons|show \d+ more)\b", re.I)
YOUTUBE_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)"
                        r"([A-Za-z0-9_-]{11})")


@dataclass
class PageRead:
    url: str
    final_url: str
    title: str
    text: str
    youtube_ids: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    needs_login: bool = False
    challenged: bool = False


def _launch(p, headless: bool):
    """Installed Google Chrome if there is one (Google sign-in refuses automation-flagged
    Chromium), else Playwright's Chromium."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    common = dict(
        headless=headless,
        viewport={"width": 1366, "height": 900},
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
    )
    try:
        return p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", **common)
    except Exception:
        return p.chromium.launch_persistent_context(str(PROFILE_DIR), **common)


def login(urls: list[str]) -> None:
    """Open a visible browser on each login page; you sign in; press Enter here when done."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        pages = ctx.pages or [ctx.new_page()]
        for i, url in enumerate(urls):
            page = pages[0] if i == 0 else ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except Exception as e:  # a slow site shouldn't stop the others
                print(f"  couldn't open {url}: {e.__class__.__name__}")
        input("\nSign in on each tab in the browser window, then press Enter here to save... ")
        ctx.close()
    print(f"Saved. Logins are kept in {PROFILE_DIR} (this laptop only).")


def read_course(url: str, headless: bool = True, follow: int = 0, delay: float = 1.0) -> list[PageRead]:
    """Read the page (and up to `follow` more course pages linked from it, same site)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            first = _read_one(page, url)
            reads = [first]
            if first.needs_login or first.challenged:
                return reads
            prefix = _course_prefix(first.final_url)
            queue = [l for l in first.links if l.startswith(prefix) and l != first.final_url]
            seen = {first.url, first.final_url}
            while queue and len(reads) <= follow:
                nxt = queue.pop(0)
                if nxt in seen:
                    continue
                seen.add(nxt)
                time.sleep(delay)   # be gentle: one page at a time, like a person would
                r = _read_one(page, nxt)
                if r.needs_login or r.challenged:
                    break
                reads.append(r)
                queue += [l for l in r.links if l.startswith(prefix) and l not in seen]
            return reads
        finally:
            ctx.close()


def _course_prefix(url: str) -> str:
    """Links under the same course: same host and the same first two path parts."""
    u = urlparse(url)
    parts = [x for x in u.path.split("/") if x][:2]
    return f"{u.scheme}://{u.netloc}/" + "/".join(parts)


def _read_one(page, url: str) -> PageRead:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    _settle(page)
    body_text = _safe(lambda: page.inner_text("body"), "")
    if CHALLENGE_HINTS.search(body_text[:2000]):
        return PageRead(url, page.url, page.title(), body_text, challenged=True)
    if _looks_logged_out(page):
        return PageRead(url, page.url, page.title(), body_text, needs_login=True)

    _expand_everything(page, url)
    _scroll_everything(page)
    _expand_everything(page, url)       # lazy lists can reveal more collapsed weeks

    texts = [_safe(lambda: page.inner_text("body"), "")]
    for frame in page.frames[1:]:       # course players often live in iframes
        if "youtube" in (frame.url or ""):
            continue
        t = _safe(lambda: frame.inner_text("body"), "")
        if t.strip():
            texts.append(f"--- frame: {frame.url} ---\n{t}")

    html = _safe(lambda: page.content(), "")
    frame_urls = " ".join(f.url or "" for f in page.frames)
    ids = list(dict.fromkeys(YOUTUBE_ID.findall(html + " " + frame_urls)))
    hrefs = _safe(lambda: page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"), [])
    links = list(dict.fromkeys(urljoin(page.url, h).split("#")[0] for h in hrefs if h.startswith("http")))
    return PageRead(url, page.url, page.title(), "\n".join(texts), ids, links)


def _looks_logged_out(page) -> bool:
    if LOGIN_HINTS.search(urlparse(page.url).path):
        return True
    # a visible password box on a course page = we got the login screen
    return bool(_safe(lambda: page.locator("input[type=password]:visible").count(), 0))


def _settle(page, timeout: int = 12_000) -> None:
    _safe(lambda: page.wait_for_load_state("networkidle", timeout=timeout))
    page.wait_for_timeout(800)


def _expand_everything(page, url: str, rounds: int = 3, max_clicks: int = 300) -> None:
    """Open collapsed weeks / modules / 'show more'. Only toggles, never navigation."""
    start_url = page.url
    for _ in range(rounds):
        clicked = 0
        _safe(lambda: page.evaluate("document.querySelectorAll('details:not([open])')"
                                    ".forEach(d => d.open = true)"))
        candidates = page.locator(
            "[aria-expanded=false]:visible, button:visible, [role=button]:visible"
        )
        n = min(_safe(lambda: candidates.count(), 0), 1500)
        for i in range(n):
            if clicked >= max_clicks:
                break
            el = candidates.nth(i)
            try:
                expanded = el.get_attribute("aria-expanded", timeout=500)
                label = (el.inner_text(timeout=500) or "")[:60]
                if expanded != "false" and not EXPAND_TEXT.search(label):
                    continue
                if re.search(r"log ?out|sign ?out|delete|submit|buy|enrol|enroll|pay", label, re.I):
                    continue
                el.click(timeout=1500)
                clicked += 1
                page.wait_for_timeout(120)
                if page.url != start_url:           # a toggle navigated: go back
                    page.go_back(wait_until="domcontentloaded")
                    _settle(page, 5000)
            except Exception:
                continue
        if not clicked:
            break
        _settle(page, 5000)


def _scroll_everything(page, max_steps: int = 40) -> None:
    """Scroll the page and any tall inner list to the bottom until nothing new loads."""
    last = -1
    for _ in range(max_steps):
        height = _safe(lambda: page.evaluate("""() => {
            window.scrollTo(0, document.body.scrollHeight);
            let h = document.body.scrollHeight;
            for (const el of document.querySelectorAll('*')) {
              const s = getComputedStyle(el);
              if ((s.overflowY === 'auto' || s.overflowY === 'scroll') &&
                  el.scrollHeight > el.clientHeight + 50) { el.scrollTop = el.scrollHeight; h += el.scrollHeight; }
            }
            return h;   // inner lists grow without the page growing
        }"""), 0)
        page.wait_for_timeout(400)
        if height == last:
            break
        last = height


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default
