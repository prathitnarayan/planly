// Does the syncing, so it keeps going after you close the popup.
import { api } from "./lib/api.js";
import { readCoursePage } from "./lib/reader.js";
import { originPattern, site } from "./lib/sites.js";

const AUTO_SYNC_HOURS = 6;

async function getWatched() {
  const { watched } = await chrome.storage.local.get("watched");
  return watched || {};           // { [pageUrl]: { goalId, goalTitle, auto, lastSync, status, lines } }
}

async function setWatched(url, patch) {
  const watched = await getWatched();
  watched[url] = { ...(watched[url] || {}), ...patch };
  await chrome.storage.local.set({ watched });
  return watched[url];
}

export async function hasSite(url) {
  return chrome.permissions.contains({ origins: [originPattern(url)] });
}

function badge(tabId, text) {
  chrome.action.setBadgeBackgroundColor({ color: "#111111", tabId }).catch(() => {});
  chrome.action.setBadgeText({ text, tabId }).catch(() => {});
}

async function syncTab(tabId, url, goalId, goalTitle, auto) {
  if (!(await hasSite(url))) throw new Error("Planly is off on this site. Switch it on first.");
  await setWatched(url, { goalId, goalTitle, auto, status: "syncing", lines: ["Reading the page..."] });
  badge(tabId, "…");
  try {
    const [{ result: page }] = await chrome.scripting.executeScript({ target: { tabId }, func: readCoursePage });
    if (page.loginLike) throw new Error(`This looks like a login page. Sign in to ${site(url).label} in this tab, then sync again.`);
    await setWatched(url, { lines: [`Read ${page.text.length.toLocaleString()} characters. Planly is listing the lectures...`] });
    const result = await api("POST", `/goals/${goalId}/sources/page`, {
      url, platform: site(url).name, title: page.title || null, text: page.text, youtube_ids: page.youtubeIds,
    });
    const lines = [
      ...result.messages,
      ...result.key_dates_added.map((k) => `+ deadline: ${k}`),
      ...result.key_dates_moved.map((k) => `~ deadline moved: ${k}`),
    ];
    badge(tabId, "✓");
    trackedCache = null;                      // new lectures may now be tracked
    return await setWatched(url, { status: "done", lastSync: Date.now(), lines });
  } catch (e) {
    badge(tabId, "!");
    await setWatched(url, { status: "error", lines: [e.message || String(e)] });
    throw e;
  }
}

// ---------- watch evidence + the on-page bar ----------

const CONTENT_ID = (origin) => "planly-watch-" + origin.replace(/[^a-z0-9]/gi, "_");
const STATIC_HOSTS = new Set(chrome.runtime.getManifest().host_permissions);

/** Register the watch tracker for exactly the sites the user switched on. */
async function syncContentScripts() {
  const { origins = [] } = await chrome.permissions.getAll();
  const wanted = origins.filter((o) => !STATIC_HOSTS.has(o));
  const have = await chrome.scripting.getRegisteredContentScripts();
  const stale = have.filter((c) => c.id.startsWith("planly-watch-") && !wanted.some((o) => CONTENT_ID(o) === c.id));
  if (stale.length) await chrome.scripting.unregisterContentScripts({ ids: stale.map((c) => c.id) });
  const missing = wanted.filter((o) => !have.some((c) => c.id === CONTENT_ID(o)));
  if (missing.length) {
    await chrome.scripting.registerContentScripts(missing.map((o) => ({
      id: CONTENT_ID(o), matches: [o], js: ["content/watch.js"], allFrames: true, runAt: "document_idle",
    })));
  }
}
// Every time the service worker wakes, make sure the tracker is registered for the sites you
// switched on, so it starts by itself on every visit and refresh (real bug 9 Oct: after the bar was
// closed it never came back, because the registration had been lost and only the one-off
// injection from the popup had been running).
syncContentScripts().catch(() => {});
chrome.runtime.onInstalled.addListener(() => syncContentScripts().catch(() => {}));
chrome.runtime.onStartup.addListener(() => syncContentScripts().catch(() => {}));
chrome.permissions.onAdded.addListener(() => syncContentScripts().catch(() => {}));
chrome.permissions.onRemoved.addListener(() => syncContentScripts().catch(() => {}));

// Which lectures may be recorded: only those in your synced courses (GET /evidence/tracked).
// Kept in storage too, so it still works while Planly is asleep (free Render plan).
const TRACKED_MINUTES = 10;
let trackedCache = null;   // { at, videos: [], pages: [] }

async function getTracked() {
  if (trackedCache && Date.now() - trackedCache.at < TRACKED_MINUTES * 60_000) return trackedCache;
  try {
    const t = await api("GET", "/evidence/tracked");
    trackedCache = { at: Date.now(), videos: t.videos || [], pages: t.pages || [], courses: t.courses || [] };
    await chrome.storage.local.set({ tracked: trackedCache });
  } catch {
    const { tracked } = await chrome.storage.local.get("tracked");
    trackedCache = tracked || { at: Date.now(), videos: [], pages: [] };   // offline: last known list
  }
  return trackedCache;
}

const isTracked = (t, key) => !!key && (key.startsWith("yt:") ? t.videos.includes(key)
  : t.pages.includes(key.split("#")[0]));

// Evidence is queued in storage so nothing is lost if Planly is asleep (free Render plan).
async function sendWatch(events) {
  const { watchQueue = [] } = await chrome.storage.local.get("watchQueue");
  const t = await getTracked();
  // also drops anything an older version queued from outside your courses
  const queue = [...watchQueue, ...events].filter((e) => isTracked(t, e.key)).slice(-500);
  try {
    for (let i = 0; i < queue.length; i += 50) await api("POST", "/evidence/watch", { events: queue.slice(i, i + 50) });
    await chrome.storage.local.set({ watchQueue: [] });
    todayCache = null;
  } catch {
    await chrome.storage.local.set({ watchQueue: queue });
  }
}

let todayCache = null;   // { at, data }
const localDay = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

async function lookup(keys) {
  if (!todayCache || Date.now() - todayCache.at > 60_000) {
    todayCache = { at: Date.now(), data: await api("GET", `/today?today=${localDay()}`) };
  }
  for (const g of todayCache.data) {
    for (const s of g.today.sessions) {
      const item = s.items.find((it) => it.video_key && keys.includes(it.video_key));
      if (item) {
        return { goalId: g.goal_id, goal: g.goal, day: g.today.day, session: s, item,
                 watched: g.today.watched[item.video_key] ?? null };
      }
    }
  }
  return null;
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "watch") {
    sendWatch(msg.events).finally(() => reply({ ok: true }));
    return true;
  }
  if (msg.type === "tracked") {
    getTracked().then((t) => reply({ videos: t.videos, pages: t.pages, courses: t.courses || [] }))
      .catch(() => reply({ videos: [], pages: [] }));
    return true;
  }
  if (msg.type === "lookup") {
    lookup(msg.keys || []).then((match) => reply({ match }))
      .catch((e) => reply({ match: null, error: e.message || String(e) }));
    return true;
  }
  if (msg.type === "tick") {
    api("PUT", `/goals/${msg.goalId}/ticks`, { day: msg.day, session_id: msg.sessionId, done: msg.done })
      .then(() => { todayCache = null; reply({ ok: true }); })
      .catch((e) => reply({ ok: false, error: e.message || String(e) }));
    return true;
  }
  if (msg.type === "site-on") {
    syncContentScripts()
      .then(() => msg.tabId && chrome.scripting.executeScript({ target: { tabId: msg.tabId, allFrames: true }, files: ["content/watch.js"] }))
      .then(() => reply({ ok: true }))
      .catch((e) => reply({ ok: false, error: e.message || String(e) }));
    return true;
  }
  if (msg.type === "sync") {
    syncTab(msg.tabId, msg.url, msg.goalId, msg.goalTitle, msg.auto)
      .then((w) => reply({ ok: true, watched: w }))
      .catch((e) => reply({ ok: false, error: e.message || String(e) }));
    return true;   // reply comes later
  }
  if (msg.type === "forget-site") {
    getWatched().then(async (watched) => {
      for (const url of Object.keys(watched)) if (originPattern(url) === msg.origin) delete watched[url];
      await chrome.storage.local.set({ watched });
      reply({ ok: true });
    });
    return true;
  }
});

// Auto-sync: when you open a page you've synced before (and left auto on), re-read it
// quietly at most every few hours. Only fires on sites you switched on.
chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (info.status !== "complete" || !tab.url) return;
  const watched = await getWatched();
  const w = watched[tab.url];
  if (!w || !w.auto || w.status === "syncing") return;
  if (w.lastSync && Date.now() - w.lastSync < AUTO_SYNC_HOURS * 3600 * 1000) return;
  if (!(await hasSite(tab.url))) return;
  syncTab(tabId, tab.url, w.goalId, w.goalTitle, true).catch(() => {});
});
