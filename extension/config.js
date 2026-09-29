// Defaults. You can change all of these in the popup's Settings, no file editing needed.
// The anon key is PUBLIC by design (it's also in the Vercel frontend). Never put a
// service_role / secret key here.
export const DEFAULTS = {
  API_URL: "https://planly-u3y3.onrender.com",
  APP_URL: "https://planly-ivory.vercel.app",
  SUPABASE_URL: "https://tuugdiuqpdwugsocvgbe.supabase.co",
  SUPABASE_ANON_KEY: "",
};

export async function getConfig() {
  const { settings } = await chrome.storage.local.get("settings");
  return { ...DEFAULTS, ...(settings || {}) };
}

export async function saveConfig(partial) {
  const cfg = await getConfig();
  const next = { ...cfg, ...partial };
  for (const k of ["API_URL", "APP_URL", "SUPABASE_URL"]) next[k] = (next[k] || "").trim().replace(/\/+$/, "");
  next.SUPABASE_ANON_KEY = (next.SUPABASE_ANON_KEY || "").trim();
  await chrome.storage.local.set({ settings: next });
  return next;
}
