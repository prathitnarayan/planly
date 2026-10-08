// Planly watch tracker. Runs ONLY on sites you switched on in the Planly popup, and records ONLY
// lectures that are in one of your synced courses / playlists (the "tracked" list from Planly).
// Any other video on the site (your music, a random YouTube video) is never recorded or sent.
// Records which parts of each tracked lecture actually played:
//   - only while the tab is visible (a background tab playing audio doesn't count)
//   - skipping ahead starts a new range; the skipped part isn't counted
//   - ads (YouTube) and speeds above 2x aren't counted
// Nothing else on the page is read. The Planly bar (bottom right) shows it's on.
(() => {
  if (window.__planlyWatch) return;
  window.__planlyWatch = true;

  const TOP = window === window.top;
  const MAX_RATE = 2.0;
  const states = new Map();          // video element -> { key, segStart, lastT }
  let lastPlayed = null;             // the video most recently playing (for page time)
  let pending = {};                  // key -> { key, url, title, duration_s, intervals: [] }
  let index = 0;

  // ---------- which lectures may be recorded (from your synced courses) ----------
  // Until the list arrives, nothing is recorded: the safe default.
  const tracked = { videos: new Set(), pages: new Set(), courses: new Map(), ready: false };
  async function loadTracked() {
    try {
      const res = await chrome.runtime.sendMessage({ type: "tracked" });
      if (res && Array.isArray(res.videos)) {
        tracked.videos = new Set(res.videos);
        tracked.pages = new Set(res.pages || []);
        tracked.courses = new Map((res.courses || []).map((c) => [c.key, c]));
        tracked.ready = true;
      }
    } catch {}
  }
  const isTracked = (key) => tracked.ready && !!key &&
    (key.startsWith("yt:") ? tracked.videos.has(key) : tracked.pages.has(key.split("#")[0]));
  // Same rules as the server's source_key(): which course a URL is, ignoring tracking / UI noise.
  const NOISE = new Set(["si", "feature", "pp", "index", "t", "start_radio", "ref", "fbclid", "gclid",
                         "activetab", "tab", "source", "referrer"]);
  function courseKey(href) {
    try {
      const u = new URL(href);
      const host = u.hostname.toLowerCase().replace(/^www\./, "").replace(/^m\./, "");
      const list = u.searchParams.get("list");
      if (["youtube.com", "youtu.be", "music.youtube.com"].includes(host) && list) return "ytlist:" + list;
      const q = [...u.searchParams].filter(([k]) => !NOISE.has(k.toLowerCase()) && !k.toLowerCase().startsWith("utm_"))
        .sort((a, b) => (a[0] + "\u0000" + a[1] < b[0] + "\u0000" + b[1] ? -1 : 1));
      const path = (u.pathname || "/").replace(/\/+$/, "") || "/";
      return host + path + (q.length ? "?" + new URLSearchParams(q).toString() : "");
    } catch { return null; }
  }
  const courseHere = () => (tracked.ready ? tracked.courses.get(courseKey(location.href)) || null : null);
  loadTracked();
  setInterval(loadTracked, 5 * 60 * 1000);

  const ytId = (href) => {
    const m = (href || "").match(/(?:youtube(?:-nocookie)?\.com\/(?:embed\/|shorts\/|watch\?(?:.*&)?v=)|youtu\.be\/)([A-Za-z0-9_-]{11})/);
    return m ? m[1] : null;
  };

  function keyFor(video) {
    const id = /youtube/.test(location.hostname) ? ytId(location.href) : null;
    if (id) return "yt:" + id;
    if (!video.__planlyIndex) video.__planlyIndex = ++index;
    return "page:" + location.origin + location.pathname + "#" + video.__planlyIndex;
  }

  function title() {
    const t = (document.title || "").replace(/ - YouTube$/, "").trim();
    return t.slice(0, 300) || null;
  }

  function adPlaying() {
    return !!document.querySelector(".ad-showing, .ad-interrupting");
  }

  function close(video, st) {
    if (st.segStart != null && st.lastT != null && st.lastT - st.segStart >= 1 && isTracked(st.key)) {
      const dur = Number.isFinite(video.duration) ? video.duration : 0;
      if (dur > 0) {
        const p = (pending[st.key] ||= { key: st.key, url: location.href.slice(0, 2000), title: title(),
                                         duration_s: Math.min(dur, 86400), intervals: [] });
        p.intervals.push([st.segStart, st.lastT]);
      }
    }
    st.segStart = null;
  }

  function tick(video) {
    let st = states.get(video);
    if (!st) states.set(video, (st = { key: keyFor(video), segStart: null, lastT: null }));
    const key = keyFor(video);
    const counting = isTracked(key) && !video.paused && !video.ended && document.visibilityState === "visible"
      && video.playbackRate <= MAX_RATE && !adPlaying() && Number.isFinite(video.duration);
    const t = video.currentTime;
    if (key !== st.key) { close(video, st); st.key = key; }            // YouTube moved to the next video
    if (!counting) { close(video, st); st.lastT = t; return; }
    lastPlayed = video;
    if (st.segStart == null) { st.segStart = t; st.lastT = t; return; }
    if (t < st.lastT - 0.5 || t - st.lastT > 3 * Math.max(1, video.playbackRate)) {   // seek
      close(video, st);
      st.segStart = t;
    }
    st.lastT = t;
  }

  function watchVideo(video) {
    if (video.__planlyTracked) return;
    video.__planlyTracked = true;
    for (const ev of ["timeupdate", "pause", "ended", "seeking", "ratechange"]) {
      video.addEventListener(ev, () => tick(video), { passive: true });
    }
  }

  function scan() { document.querySelectorAll("video").forEach(watchVideo); }
  scan();
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });

  function flush() {
    for (const [video, st] of states) {                               // include the running segment
      if (st.segStart != null) { close(video, st); if (!video.paused) st.segStart = video.currentTime; }
    }
    const events = Object.values(pending).filter((e) => isTracked(e.key) && (e.intervals.length || e.active_s > 0));
    pending = {};
    if (events.length) {
      try { chrome.runtime.sendMessage({ type: "watch", events }); } catch {}
    }
  }
  setInterval(flush, 20000);
  document.addEventListener("visibilitychange", () => { states.forEach((st, v) => tick(v)); flush(); });
  window.addEventListener("pagehide", flush);

  // ---------- real time on the lecture page (top frame only, so embeds aren't counted twice) ----------
  // Counted while the tab is visible and focused, and something is happening (a video playing,
  // or you touched the page in the last 5 minutes). Pauses for notes count; a forgotten tab doesn't.
  // This is how Planly learns your real video pace (pauses, notes, rewinds included).
  if (TOP) {
    let lastInput = Date.now();
    for (const ev of ["mousemove", "keydown", "scroll", "click", "touchstart"]) {
      window.addEventListener(ev, () => { lastInput = Date.now(); }, { passive: true, capture: true });
    }
    const pageVideo = () => {
      const own = lastPlayed || document.querySelector("video");
      if (own && Number.isFinite(own.duration)) return { key: keyFor(own), duration: own.duration };
      const f = [...document.querySelectorAll("iframe[src]")].find((x) => ytId(x.src));
      return f ? { key: "yt:" + ytId(f.src), duration: 0 } : null;
    };
    setInterval(() => {
      if (document.visibilityState !== "visible" || !document.hasFocus()) return;
      const playing = [...document.querySelectorAll("video")].some((v) => !v.paused && !v.ended);
      if (!playing && Date.now() - lastInput > 5 * 60 * 1000) return;
      const v = pageVideo();
      if (!v || !isTracked(v.key)) return;
      const p = (pending[v.key] ||= { key: v.key, url: location.href.slice(0, 2000), title: title(),
                                      duration_s: Math.min(v.duration || 0, 86400), intervals: [] });
      p.active_s = (p.active_s || 0) + 5;
    }, 5000);
  }

  if (TOP) setTimeout(() => startBar(), 800);
  const trackedHere = () => pageKeys().some(isTracked);

  // ---------- the Planly bar (top frame only) ----------
  function pageKeys() {
    const keys = new Set();
    document.querySelectorAll("video").forEach((v) => keys.add(keyFor(v)));
    document.querySelectorAll("iframe[src]").forEach((f) => { const id = ytId(f.src); if (id) keys.add("yt:" + id); });
    return [...keys];
  }

  function startBar() {
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;display:none;";
    const root = host.attachShadow({ mode: "closed" });
    root.innerHTML = `
      <style>
        :host { all: initial; }
        .bar { font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111;
               background: #fff; border: 1px solid #111; border-radius: 12px; padding: 10px 12px;
               box-shadow: 0 6px 24px rgba(0,0,0,.18); max-width: 340px; display: flex; gap: 10px; align-items: center; }
        @media (prefers-color-scheme: dark) { .bar { color: #f2f2f2; background: #111; border-color: #f2f2f2; } }
        .logo { font-weight: 800; letter-spacing: -.02em; }
        .muted { opacity: .6; font-size: 12px; }
        .grow { flex: 1; min-width: 0; }
        .t { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .meter { height: 4px; border-radius: 4px; background: rgba(127,127,127,.3); margin-top: 5px; overflow: hidden; }
        .fill { height: 4px; background: currentColor; transition: width .5s; }
        button.box { all: unset; cursor: pointer; width: 22px; height: 22px; border: 1.5px solid currentColor;
                     border-radius: 6px; display: flex; align-items: center; justify-content: center; font-weight: 800; }
        button.box span { visibility: hidden; }
        button.box[aria-checked="true"] { background: currentColor; }
        button.box[aria-checked="true"] span { visibility: visible; }
        button.box[aria-checked="true"] span { color: #fff; }
        @media (prefers-color-scheme: dark) { button.box[aria-checked="true"] span { color: #111; } }
        button.box:disabled { cursor: not-allowed; opacity: .45; background-image: repeating-linear-gradient(135deg, rgba(127,127,127,.5) 0 2px, transparent 2px 6px); }
        [hidden] { display: none !important; }
        button.x { all: unset; cursor: pointer; opacity: .5; padding: 0 2px; }
        .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: currentColor; margin-right: 5px; animation: b 2s infinite; }
        @keyframes b { 50% { opacity: .25; } }
      </style>
      <div class="bar" role="status">
        <span class="logo">p.</span>
        <div class="grow"><div class="t" id="line"><span class="dot"></span>Planly is recording this lecture</div>
          <div class="muted t" id="sub"></div><div class="meter" id="meter" hidden><div class="fill" id="fill"></div></div></div>
        <button class="box" id="box" role="checkbox" aria-checked="false" hidden title="Done in Planly"><span>✓</span></button>
        <button class="x" id="x" title="Hide">×</button>
      </div>`;
    document.documentElement.appendChild(host);
    const $ = (id) => root.getElementById(id);
    let current = null, dismissed = null, shownFor = null, mode = null;
    const pageId = () => pageKeys().filter(isTracked).sort().join(",");
    $("x").onclick = () => { dismissed = pageId(); host.style.display = "none"; };
    // The bar shows only while a tracked lecture is on the page (YouTube changes videos without
    // reloading, so this is re-checked every few seconds). Elsewhere: no bar, nothing recorded.
    // Two modes: a tracked LECTURE (recording + today's progress + tick box), or the COURSE /
    // playlist page itself (no video yet: says it's in Planly and that lectures opened from it count).
    function place() {
      const lecture = trackedHere() ? pageId() : null;
      const course = lecture ? null : courseHere();
      const here = lecture || (course ? "course:" + course.key : null);
      const show = !!here && here !== dismissed;
      host.style.display = show ? "block" : "none";
      if (show && here !== shownFor) {
        shownFor = here;
        mode = lecture ? "lecture" : "course";
        if (lecture) refresh(); else showCourse(course);
      }
      if (!here) { shownFor = null; mode = null; }
    }
    function showCourse(c) {
      current = null;
      $("line").textContent = c.title;
      $("sub").textContent = `In Planly · ${c.lectures} lecture${c.lectures === 1 ? "" : "s"} · open one to record it`;
      $("box").hidden = true; $("meter").hidden = true;
    }
    setInterval(place, 3000);

    async function refresh() {
      if (!host.isConnected) return;
      let res;
      try { res = await chrome.runtime.sendMessage({ type: "lookup", keys: pageKeys() }); } catch { return; }
      current = res && res.match;
      if (!current) {
        $("line").innerHTML = '<span class="dot"></span>Planly is recording this lecture';
        $("sub").textContent = res && res.error ? res.error : "From your course · not in today's plan.";
        $("box").hidden = true; $("meter").hidden = true;
        return;
      }
      const m = current;
      const part = m.item.part_from > 0 || m.item.part_to < 1
        ? ` (${Math.round(m.item.part_from * 100)}–${Math.round(m.item.part_to * 100)}%)` : "";
      $("line").textContent = `${m.item.title}${part}`;
      const w = m.watched == null ? null : Math.round(m.watched * 100);
      $("sub").textContent = (w == null ? "not watched yet" : `watched ${w}%`) +
        (m.session.auto ? " · ticked by evidence" : m.session.locked ? " · ticks itself at 80%" : "") +
        ` · today: ${m.session.milestone_name}`;
      $("meter").hidden = false;
      $("fill").style.width = `${w || 0}%`;
      const box = $("box");
      box.hidden = false;
      box.setAttribute("aria-checked", String(m.session.done));
      box.disabled = m.session.locked && !m.session.done;
    }

    $("box").onclick = async () => {
      if (!current) return;
      $("box").disabled = true;
      const res = await chrome.runtime.sendMessage({ type: "tick", goalId: current.goalId, day: current.day,
                                                     sessionId: current.session.id, done: !current.session.done });
      if (res && res.error) $("sub").textContent = res.error;
      refresh();
    };
    place();
    setInterval(() => { flush(); if (host.style.display !== "none" && mode === "lecture") setTimeout(refresh, 1500); }, 30000);
  }
})();
