import { getConfig, saveConfig } from "./config.js";
import { api } from "./lib/api.js";
import { currentEmail, NotSignedIn, signIn, signOut } from "./lib/auth.js";
import { isWebPage, originPattern, site } from "./lib/sites.js";

const $ = (id) => document.getElementById(id);
const show = (id, on = true) => ($(id).hidden = !on);
let tab = null;
let cfg = null;

function error(msg) {
  $("error").textContent = msg || "";
  show("error", !!msg);
}

async function watchedFor(url) {
  const { watched } = await chrome.storage.local.get("watched");
  return (watched || {})[url] || null;
}

function renderStatus(w) {
  if (!w) return show("status", false);
  show("status");
  const when = w.lastSync ? new Date(w.lastSync).toLocaleString() : "";
  $("status-title").textContent =
    w.status === "syncing" ? "Syncing… (you can close this)" :
    w.status === "error" ? "Last sync failed" : `Synced ${when}`;
  $("status-lines").textContent = (w.lines || []).join("\n");
  const link = $("open-plan");
  if (w.goalId && w.status === "done") {
    link.href = `${cfg.APP_URL}/goals/${w.goalId}`;
    show("open-plan");
  } else show("open-plan", false);
  $("sync").disabled = w.status === "syncing";
}

// ---------- settings ----------

function fillSettings() {
  $("s-api").value = cfg.API_URL;
  $("s-app").value = cfg.APP_URL;
  $("s-sb").value = cfg.SUPABASE_URL;
  $("s-key").value = cfg.SUPABASE_ANON_KEY;
}

$("gear").onclick = () => { fillSettings(); show("settings", $("settings").hidden); };
$("s-save").onclick = async () => {
  cfg = await saveConfig({
    API_URL: $("s-api").value, APP_URL: $("s-app").value,
    SUPABASE_URL: $("s-sb").value, SUPABASE_ANON_KEY: $("s-key").value,
  });
  show("settings", false);
  start();
};
$("s-signout").onclick = async () => { await signOut(); show("settings", false); start(); };

// ---------- sign in ----------

$("signin-btn").onclick = async () => {
  error("");
  $("signin-btn").disabled = true;
  try {
    await signIn($("email").value.trim(), $("password").value);
    $("password").value = "";
    start();
  } catch (e) {
    error(e.message);
  } finally {
    $("signin-btn").disabled = false;
  }
};

// ---------- this site ----------

async function renderSite() {
  const s = site(tab.url);
  $("site-name").textContent = s.label;
  $("site-host").textContent = s.host;
  const on = await chrome.permissions.contains({ origins: [originPattern(tab.url)] });
  const t = $("toggle");
  t.textContent = on ? "On" : "Off";
  t.setAttribute("aria-pressed", String(on));
  $("toggle-help").textContent = on
    ? "Planly can read pages on this site, only when you sync (or re-open a page you synced)."
    : "Planly can't see this site. Switch on to let it read the pages you sync here.";
  show("on", on);
  if (on) await loadGoals();
}

$("toggle").onclick = async () => {
  error("");
  const origin = originPattern(tab.url);
  const on = await chrome.permissions.contains({ origins: [origin] });
  if (on) {
    await chrome.permissions.remove({ origins: [origin] });
    await chrome.runtime.sendMessage({ type: "forget-site", origin });
  } else {
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) error("Chrome didn't allow it, so Planly still can't see this site.");
  }
  renderSite();
};

async function loadGoals() {
  const sel = $("goal");
  if (sel.options.length) return;
  try {
    const goals = await api("GET", "/goals");
    if (!goals.length) {
      error(`No goals yet. Create one at ${cfg.APP_URL} first.`);
      $("sync").disabled = true;
      return;
    }
    const w = await watchedFor(tab.url);
    for (const g of goals) {
      const o = new Option(g.title, g.id);
      if (w && w.goalId === g.id) o.selected = true;
      sel.add(o);
    }
    if (w) $("auto").checked = w.auto !== false;
  } catch (e) {
    if (e instanceof NotSignedIn) return start();
    error(e.message);
  }
}

$("sync").onclick = async () => {
  error("");
  const sel = $("goal");
  const goalId = sel.value;
  if (!goalId) return error("Pick a goal first.");
  $("sync").disabled = true;
  renderStatus({ status: "syncing", lines: ["Reading the page..."] });
  const res = await chrome.runtime.sendMessage({
    type: "sync", tabId: tab.id, url: tab.url, goalId,
    goalTitle: sel.options[sel.selectedIndex].text, auto: $("auto").checked,
  });
  if (!res.ok) error(res.error);
  $("sync").disabled = false;
};

// live updates while the background worker syncs
chrome.storage.onChanged.addListener(async (changes) => {
  if (changes.watched && tab) renderStatus((changes.watched.newValue || {})[tab.url]);
});

// ---------- start ----------

async function start() {
  error("");
  for (const id of ["signin", "main", "settings"]) show(id, false);
  cfg = await getConfig();
  if (!cfg.SUPABASE_ANON_KEY) {
    fillSettings();
    show("settings");
    error("One-time setup: paste the Supabase anon key (Supabase → Project Settings → API).");
    return;
  }
  const email = await currentEmail();
  $("who").textContent = email || "";
  if (!email) return show("signin");

  show("main");
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const web = tab && isWebPage(tab.url);
  show("notweb", !web);
  show("sitebox", !!web);
  if (!web) return;
  await renderSite();
  renderStatus(await watchedFor(tab.url));
}

start();
