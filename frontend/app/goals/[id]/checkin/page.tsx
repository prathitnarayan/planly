"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Button, ErrorNote, Input, Loading, PageTitle } from "@/components/ui";
import { api } from "@/lib/api";
import { day, isoToday, minutesToHours } from "@/lib/format";
import type { CheckIn, Due, Replan } from "@/lib/types";

type Answer = { outcome: CheckIn["outcome"]; minutes: string };

export default function CheckInPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [due, setDue] = useState<Due | null>(null);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [finished, setFinished] = useState<Record<string, boolean>>({});
  const [result, setResult] = useState<Replan | null>(null);
  const [logged, setLogged] = useState<{ minutes: number; missed: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const today = isoToday();

  useEffect(() => {
    api<Due>(`/goals/${id}/due`, { query: { today } })
      .then((d) => {
        setDue(d);
        setAnswers(d.sessions.map((s) => ({ outcome: "done", minutes: String(s.planned_minutes) })));
      })
      .catch((e) => setError(e.message));
  }, [id, today]);

  const workedOn = useMemo(() => {
    if (!due) return [] as { key: string; name: string }[];
    const seen = new Map<string, string>();
    due.sessions.forEach((s, i) => { if (answers[i]?.outcome !== "missed") seen.set(s.milestone_key, s.milestone_name); });
    return [...seen.entries()].map(([key, name]) => ({ key, name }));
  }, [due, answers]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!due) return;
    const checkins: CheckIn[] = due.sessions.map((s, i) => {
      const a = answers[i];
      const actual = a.outcome === "missed" ? 0 : Math.max(1, parseInt(a.minutes) || 0);
      return { day: s.day, milestone_key: s.milestone_key, outcome: a.outcome, planned_minutes: s.planned_minutes, actual_minutes: actual };
    });
    for (const key of Object.keys(finished).filter((k) => finished[k])) {
      const last = [...checkins].reverse().find((c) => c.milestone_key === key && c.actual_minutes > 0);
      if (last) last.milestone_complete = true;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await api<Replan>(`/goals/${id}/checkins`, { body: { checkins, today } }));
      setLogged({
        minutes: checkins.reduce((s, c) => s + c.actual_minutes, 0),
        missed: checkins.filter((c) => c.outcome === "missed").length,
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const back = <Link href={`/goals/${id}`} className="mb-6 inline-block text-sm text-muted hover:text-ink">← Plan</Link>;

  if (result) {
    return (
      <>
        {back}
        <PageTitle kicker="Checked in" sub={`The plan now starts ${day(result.today)}.`}>
          {result.alerts.length ? "Heads up" : "✓ Still on track"}
        </PageTitle>
        {logged && (
          <p className="num mb-6 text-sm">
            Logged <strong>{minutesToHours(logged.minutes)}</strong>
            {logged.missed > 0 && <> · {logged.missed} missed — that work is back in the plan</>}
          </p>
        )}
        {result.alerts.length > 0 && (
          <ul className="mb-6 space-y-2 border-l-2 border-ink pl-4 text-sm">{result.alerts.map((a) => <li key={a}>{a}</li>)}</ul>
        )}
        <ul className="mb-8 divide-y divide-line border-y border-line">
          {result.progress.filter((p) => p.spent_minutes > 0 || p.complete).map((p) => (
            <li key={p.key} className="flex items-baseline justify-between gap-4 py-3 text-sm">
              <span>{p.name}</span>
              <span className="num shrink-0 text-muted">
                {p.complete ? "✓ finished" : `${minutesToHours(p.spent_minutes)} done · ${minutesToHours(p.remaining_minutes)} left`}
              </span>
            </li>
          ))}
        </ul>
        <Button onClick={() => router.push(`/goals/${id}`)}>See the updated plan</Button>
      </>
    );
  }

  if (!due) return <>{back}{error ? <ErrorNote error={error} /> : <Loading />}</>;

  if (due.sessions.length === 0) {
    return <>{back}<PageTitle kicker="Check in" sub={due.message ?? undefined}>Nothing to check in yet</PageTitle></>;
  }

  const set = (i: number, patch: Partial<Answer>) => setAnswers(answers.map((a, j) => (j === i ? { ...a, ...patch } : a)));

  return (
    <>
      {back}
      <PageTitle kicker="Check in" sub={`${day(due.since)} – ${day(due.until)}. What actually happened? Honest beats hopeful.`}>
        How did it go?
      </PageTitle>
      <form onSubmit={submit}>
        <ul className="divide-y divide-line border-y border-line">
          {due.sessions.map((s, i) => (
            <li key={`${s.day}-${s.milestone_key}`} className="py-4">
              <div className="mb-3 flex items-baseline justify-between gap-3">
                <span className="text-sm font-medium">{s.milestone_name}</span>
                <span className="num shrink-0 text-xs text-muted">{day(s.day)} · {s.planned_minutes} min</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {(["done", "partial", "missed"] as const).map((o) => (
                  <button type="button" key={o} onClick={() => set(i, { outcome: o, minutes: o === "done" ? String(s.planned_minutes) : answers[i].minutes })}
                          aria-pressed={answers[i]?.outcome === o}
                          className={`h-9 cursor-pointer rounded-md border px-3 text-sm capitalize ${answers[i]?.outcome === o ? "border-ink bg-ink text-paper" : "border-line hover:border-ink"}`}>
                    {o}
                  </button>
                ))}
                {answers[i]?.outcome !== "missed" && (
                  <span className="flex items-center gap-2 text-sm text-muted">
                    <Input className="!h-9 w-20 text-right num" inputMode="numeric" value={answers[i]?.minutes ?? ""}
                           onChange={(e) => set(i, { minutes: e.target.value })} aria-label="actual minutes" />
                    min
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>

        {workedOn.length > 0 && (
          <fieldset className="mt-6">
            <legend className="mb-2 text-sm font-medium">Finished any of these completely?</legend>
            {workedOn.map((m) => (
              <label key={m.key} className="flex cursor-pointer items-center gap-2 py-1 text-sm">
                <input type="checkbox" className="h-4 w-4 accent-[var(--ink)]" checked={!!finished[m.key]}
                       onChange={(e) => setFinished({ ...finished, [m.key]: e.target.checked })} />
                {m.name}
              </label>
            ))}
          </fieldset>
        )}
        <Button className="mt-8" disabled={busy}>{busy ? "Replanning…" : "Check in"}</Button>
      </form>
      <ErrorNote error={error} />
    </>
  );
}
