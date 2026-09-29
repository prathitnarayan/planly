// Sign in to PLANLY (your Supabase account), not to course sites. Course sites use the
// login already in your Chrome. Only Planly's refresh token is kept, in chrome.storage.local.
import { getConfig } from "../config.js";

export class NotSignedIn extends Error {}

async function authCall(path, body) {
  const cfg = await getConfig();
  if (!cfg.SUPABASE_URL || !cfg.SUPABASE_ANON_KEY) throw new NotSignedIn("Open Settings and add the Supabase anon key.");
  const res = await fetch(`${cfg.SUPABASE_URL}/auth/v1/${path}`, {
    method: "POST",
    headers: { apikey: cfg.SUPABASE_ANON_KEY, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new NotSignedIn(data.msg || data.error_description || data.message || `Sign-in failed (${res.status})`);
  return data;
}

async function store(data, email) {
  const session = {
    access_token: data.access_token,
    refresh_token: data.refresh_token,
    expires_at: data.expires_at || Math.floor(Date.now() / 1000) + (data.expires_in || 3600),
    email: email || data.user?.email,
  };
  await chrome.storage.local.set({ session });
  return session;
}

export async function signIn(email, password) {
  return store(await authCall("token?grant_type=password", { email, password }), email);
}

export async function signOut() {
  await chrome.storage.local.remove("session");
}

export async function currentEmail() {
  const { session } = await chrome.storage.local.get("session");
  return session?.email || null;
}

export async function getToken() {
  const { session } = await chrome.storage.local.get("session");
  if (!session) throw new NotSignedIn("Sign in to Planly first.");
  if (session.expires_at - 60 > Date.now() / 1000) return session.access_token;
  try {
    const fresh = await store(await authCall("token?grant_type=refresh_token", { refresh_token: session.refresh_token }), session.email);
    return fresh.access_token;
  } catch (e) {
    await signOut();
    throw new NotSignedIn("Your Planly sign-in expired. Sign in again.");
  }
}
