import { getConfig } from "../config.js";
import { getToken } from "./auth.js";

export async function api(method, path, body) {
  const cfg = await getConfig();
  const token = await getToken();
  let res;
  try {
    res = await fetch(`${cfg.API_URL}${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(`Can't reach Planly at ${cfg.API_URL}. If it's the free Render plan, it may be waking up: try again in a minute.`);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
    if (res.status === 404 && detail === "Not Found") {
      throw new Error(`Your Planly backend (${cfg.API_URL}) is an older version without course sync. ` +
        "Push the latest code so Render redeploys, then try again.");
    }
    throw new Error(`Planly said ${res.status}: ${detail}`);
  }
  return data;
}
