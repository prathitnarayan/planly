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
  let res: Response;
  try {
    res = await fetch(`${API}${path}${qs}`, {
      method: init.method ?? (init.body === undefined ? "GET" : "POST"),
      headers: { "Content-Type": "application/json", ...(await authHeader()) },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    // The browser couldn't connect at all (server down, wrong URL, or blocked by CORS).
    throw new ApiError(0,
      `Can't reach the Planly API at ${API}. Is the backend running? ` +
      `(cd backend && python -m uvicorn app.main:app --reload --reload-dir app). ` +
      `Also open the app at http://localhost:3000, not 127.0.0.1 or a network address.`);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}
