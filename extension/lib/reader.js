// Runs INSIDE the course page (only on sites you switched on, only when you sync).
// Opens collapsed weeks/modules, clicks "show more", scrolls lazy lists, then takes the
// visible text + embedded YouTube ids. It never navigates, submits, downloads or buys.
// Must be self-contained: Chrome copies this one function into the page.
export async function readCoursePage() {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const EXPAND = /^\s*(expand all|expand|show more|load more|view more|see more|see all|show all|view all|more lessons|show \d+ more)\b/i;
  const DANGER = /log ?out|sign ?out|delete|submit|buy|enrol|enroll|pay|unsubscribe|reset/i;
  const startPath = location.pathname;
  let moved = false;

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  async function expandAll() {
    for (let round = 0; round < 3; round++) {
      let clicked = 0;
      document.querySelectorAll("details:not([open])").forEach((d) => (d.open = true));
      const els = document.querySelectorAll('[aria-expanded="false"], button, [role="button"]');
      for (const el of Array.from(els).slice(0, 1500)) {
        if (clicked >= 300) break;
        if (!visible(el)) continue;
        const label = (el.innerText || el.getAttribute("aria-label") || "").slice(0, 60);
        const collapsed = el.getAttribute("aria-expanded") === "false";
        if (!collapsed && !EXPAND.test(label)) continue;
        if (DANGER.test(label)) continue;
        const link = el.closest("a[href]");
        if (link && !link.getAttribute("href").startsWith("#")) continue;   // a link = navigation, never click
        try { el.click(); clicked++; } catch {}
        await sleep(120);
        // Some sheets (e.g. Striver's) put the open section in the URL (?step= / #...). That's fine.
        // If a click took us to a different page, STOP clicking. Never press Back: that
        // sent the tab to the previous page (real bug, 29 Sep).
        if (location.pathname !== startPath) { moved = true; break; }
      }
      if (!clicked || moved) break;
      await sleep(800);
    }
  }

  async function scrollAll() {
    let last = -1;
    for (let i = 0; i < 40; i++) {
      window.scrollTo(0, document.body.scrollHeight);
      let h = document.body.scrollHeight;
      for (const el of document.querySelectorAll("*")) {
        const s = getComputedStyle(el);
        if ((s.overflowY === "auto" || s.overflowY === "scroll") && el.scrollHeight > el.clientHeight + 50) {
          el.scrollTop = el.scrollHeight;
          h += el.scrollHeight;          // inner lists grow without the page growing
        }
      }
      await sleep(500);
      if (h === last) break;
      last = h;
    }
    window.scrollTo(0, 0);
  }

  const y = window.scrollY;
  await expandAll();
  await scrollAll();
  await expandAll();
  window.scrollTo(0, y);

  const texts = [document.body.innerText];
  for (const f of document.querySelectorAll("iframe")) {
    try {                         // same-site frames only; others are off limits (as they should be)
      const t = f.contentDocument?.body?.innerText;
      if (t && t.trim()) texts.push(`--- frame ---\n${t}`);
    } catch {}
  }
  const ytRe = /(?:youtube(?:-nocookie)?\.com\/(?:embed\/|watch\?v=|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/g;
  const srcs = [
    ...Array.from(document.querySelectorAll("iframe[src]"), (f) => f.src),
    ...Array.from(document.querySelectorAll("a[href]"), (a) => a.href),
    document.documentElement.innerHTML.slice(0, 3_000_000),
  ].join(" ");
  const youtubeIds = [...new Set(Array.from(srcs.matchAll(ytRe), (m) => m[1]))].slice(0, 1000);

  const text = texts.join("\n");
  const loginLike = !!document.querySelector('input[type="password"]') && text.length < 3000;
  return { url: location.href, title: document.title, text: text.slice(0, 900_000), youtubeIds, loginLike };
}
