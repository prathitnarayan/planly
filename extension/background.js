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
    return await setWatched(url, { status: "done", lastSync: Date.now(), lines });
  } catch (e) {
    badge(tabId, "!");
    await setWatched(url, { status: "error", lines: [e.message || String(e)] });
    throw e;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
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
