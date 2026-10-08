import { supabase } from "./supabase";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function authHeader(): Promise<Record<string, string>> {
  if (!supabase) return {};
  const { data } = await supabase.auth.getSession(); // refreshes an expired token automatically
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Call the FastAPI backend as the signed-in user. Throws ApiError with the backend's message. */
export async function api<T>(path: string, init: { method?: string; body?: unknown; query?: Record<string, string> } = {}): Promise<T> {
  const qs = init.query ? `?${new URLSearchParams(init.query)}` : "";
  const method = init.method ?? (init.body === undefined ? "GET" : "POST");
  const send = async () => fetch(`${API}${path}${qs}`, {
    method,
    headers: { "Content-Type": "application/json", ...(await authHeader()) },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  let res: Response;
  try {
    res = await send();
  } catch {
    // The browser couldn't connect at all (server asleep / down, wrong URL, or blocked by CORS).
    // Reads are safe to repeat once: the free Render plan wakes up within about a minute.
    try {
      if (method !== "GET") throw new Error("no retry");
      await new Promise((r) => setTimeout(r, 4000));
      res = await send();
    } catch {
      const local = /localhost|127\.0\.0\.1/.test(API);
      throw new ApiError(0, local
        ? `Can't reach the Planly API at ${API}. Is the backend running? ` +
          `(cd backend && python -m uvicorn app.main:app --reload --reload-dir app). ` +
          `Also open the app at http://localhost:3000, not 127.0.0.1 or a network address.`
        : `Can't reach Planly's server right now. On the free plan it sleeps when idle and takes up to ` +
          `a minute to wake up — wait a moment and try again. If it keeps happening, check that the ` +
          `Render service is live.`);
    }
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {}
    if (res.status === 401 && supabase) {
      // expired / revoked / deleted account: drop the stale session and go to the login page
      await supabase.auth.signOut().catch(() => {});
      if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
        window.location.href = `/login?reason=${encodeURIComponent(msg)}`;
      }
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}
