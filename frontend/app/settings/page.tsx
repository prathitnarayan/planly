"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, ErrorNote, Loading, PageTitle } from "@/components/ui";
import { api } from "@/lib/api";
import type { NotifyPrefs, SettingsView } from "@/lib/types";

const hhmm = (t: string) => t.slice(0, 5);

/** Same clock? (Asia/Calcutta and Asia/Kolkata are the same zone under two names.) */
function sameZone(a: string, b: string): boolean {
  try {
    const d = new Date(Date.UTC(2026, 0, 15, 12)), e = new Date(Date.UTC(2026, 6, 15, 12));
    const f = (z: string, x: Date) => x.toLocaleString("en-US", { timeZone: z });
    return f(a, d) === f(b, d) && f(a, e) === f(b, e);
  } catch { return a === b; }
}

function Card({ title, status, children }: { title: string; status?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rise mb-6 rounded-2xl border border-line p-5">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h2 className="font-semibold tracking-tight">{title}</h2>
        {status && <span className="text-xs text-muted">{status}</span>}
      </div>
      {children}
    </section>
  );
}

export default function Settings() {
  const [s, setS] = useState<SettingsView | null>(null);
  const [notify, setNotify] = useState<NotifyPrefs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);
  const deviceTz = typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "";

  const load = useCallback(() => api<SettingsView>("/me/settings").then((v) => {
    setS(v);
    setNotify((n) => n ?? v.notify);
    return v;
  }), []);

  useEffect(() => {
    load().catch((e) => setError(e.message));
    const g = new URLSearchParams(window.location.search).get("google");
    if (g) setNote({ connected: "✓ Google Calendar connected (read-only).", failed: "! Google sign-in failed — try again.",
                     cancelled: "Google sign-in was cancelled." }[g] ?? null);
    return () => { if (poll.current) clearInterval(poll.current); };
  }, [load]);

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key); setError(null);
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(null); }
  }

  async function connectTelegram() {
    await run("tg", async () => {
      const { url } = await api<{ url: string }>("/me/telegram/link", { method: "POST" });
      window.open(url, "_blank");
      setNote("Telegram opened — tap Start there. This page updates by itself.");
      let tries = 0;
      poll.current = setInterval(async () => {
        const v = await load().catch(() => null);
        if (v?.telegram.linked || ++tries > 40) {
          if (poll.current) clearInterval(poll.current);
          if (v?.telegram.linked) setNote("✓ Telegram connected.");
        }
      }, 3000);
    });
  }

  if (!s || !notify) return error ? <ErrorNote error={error} /> : <Loading />;

  return (
    <>
      <Link href="/" className="mb-6 inline-block text-sm text-muted hover:text-ink">← Goals</Link>
      <PageTitle kicker="Settings" sub="Planly talks to you in one place, at most twice a day. Your calendar is only read, never written.">
        Nudges & calendar
      </PageTitle>
      {note && <p className="rise mb-6 rounded-lg border border-ink px-4 py-3 text-sm">{note}</p>}

      <Card title="Time zone" status={s.timezone}>
        <p className="text-sm text-muted">Used for when your day starts and ends, and when messages arrive.</p>
        {deviceTz && !sameZone(deviceTz, s.timezone) && (
          <Button variant="outline" className="mt-3" disabled={busy === "tz"}
                  onClick={() => run("tz", () => api("/me/settings", { method: "PUT", body: { timezone: deviceTz } }))}>
            Use this device&apos;s: {deviceTz}
          </Button>
        )}
      </Card>

      <Card title="Telegram" status={s.telegram.linked ? `connected${s.telegram.username ? ` · @${s.telegram.username}` : ""}` : "not connected"}>
        {!s.telegram.available ? (
          <p className="text-sm text-muted">Not set up on this Planly server yet (see docs/INTEGRATIONS.md).</p>
        ) : !s.telegram.linked ? (
          <>
            <p className="mb-3 text-sm">Get today&apos;s tasks in the morning and tick them from the chat. Nothing else.</p>
            <Button onClick={connectTelegram} disabled={busy === "tg"}>Connect Telegram</Button>
          </>
        ) : (
          <>
            <div className="space-y-3">
              {(["morning", "evening"] as const).map((k) => (
                <label key={k} className="flex items-center justify-between gap-4 text-sm">
                  <span className="flex items-center gap-3">
                    <input type="checkbox" checked={notify[k]} className="h-4 w-4 accent-current"
                           onChange={(e) => setNotify({ ...notify, [k]: e.target.checked })} />
                    {k === "morning" ? "Morning: today's tasks" : "Evening: only if something's still open"}
                  </span>
                  <input type="time" value={hhmm(notify[`${k}_at`])} disabled={!notify[k]}
                         onChange={(e) => setNotify({ ...notify, [`${k}_at`]: e.target.value })}
                         className="num h-9 rounded-md border border-line bg-paper px-2 text-sm disabled:opacity-40" />
                </label>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button disabled={busy === "save"} onClick={() => run("save", () => api("/me/settings", { method: "PUT", body: { notify } }))}>Save</Button>
              <Button variant="outline" disabled={busy === "test"} onClick={() => run("test", async () => {
                await api("/me/telegram/test", { method: "POST" }); setNote("Sent — check Telegram.");
              })}>Send today&apos;s list now</Button>
              <Button variant="ghost" onClick={() => run("tgoff", () => api("/me/telegram", { method: "DELETE" }))}>Disconnect</Button>
            </div>
          </>
        )}
      </Card>

      <Card title="Google Calendar" status={s.google.connected ? "connected · read-only" : "not connected"}>
        {!s.google.available ? (
          <p className="text-sm text-muted">Not set up on this Planly server yet (see docs/INTEGRATIONS.md).</p>
        ) : !s.google.connected ? (
          <>
            <p className="mb-1 text-sm">Meetings come out of your study time automatically.</p>
            <p className="mb-3 text-xs text-muted">Read-only, free/busy only: Planly sees <em>when</em> you&apos;re busy, never titles or
              people, and never adds events — so no extra calendar notifications.</p>
            <Button onClick={() => run("g", async () => {
              const { url } = await api<{ url: string }>("/me/google/connect"); window.location.href = url;
            })} disabled={busy === "g"}>Connect Google Calendar</Button>
          </>
        ) : (
          <>
            <p className="num text-sm">
              {s.google.busy_hours_next_7d != null ? `${s.google.busy_hours_next_7d} h busy in the next 7 days` : "No busy times read yet"}
              {s.google.fetched_at && <span className="text-muted"> · checked {new Date(s.google.fetched_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
            </p>
            {s.google.error && <p className="mt-2 text-sm"><strong>!</strong> Last check failed: {s.google.error}</p>}
            <Button variant="ghost" className="mt-3" onClick={() => run("goff", () => api("/me/google", { method: "DELETE" }))}>Disconnect</Button>
          </>
        )}
      </Card>
      <ErrorNote error={error} />
    </>
  );
}
