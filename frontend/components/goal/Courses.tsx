"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Input, Label, Loading, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { day, minutesToHours } from "@/lib/format";
import type { SourceItem, SourceView, SyncResult } from "@/lib/types";

const KIND_WORD: Record<string, [string, string]> = {
  video: ["video", "videos"], live: ["live class", "live classes"], reading: ["reading", "readings"],
  practice: ["problem", "problems"], test: ["test", "tests"], assignment: ["assignment", "assignments"],
  other: ["other", "other"],
};
const kindWord = (kind: string, n: number) => (KIND_WORD[kind] ?? [kind, kind])[n === 1 ? 0 : 1];

/** ISO timestamp -> local "YYYY-MM-DD" (slicing the UTC string would show yesterday late at night). */
function localDay(ts: string): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Courses feeding this goal: synced by the Chrome extension, or pasted here (public / YouTube). */
export function Courses({ goalId, onChanged }: { goalId: string; onChanged: () => void }) {
  const [sources, setSources] = useState<SourceView[] | null>(null);
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    () => api<SourceView[]>(`/goals/${goalId}/sources`).then(setSources).catch((e) => setError(e.message)),
    [goalId],
  );
  useEffect(() => { load(); }, [load]);

  async function add() {
    if (!link.trim()) return;
    setBusy(true); setError(null); setResult(null);
    try {
      const r = await api<SyncResult>(`/goals/${goalId}/sources/link`, { body: { url: link.trim() } });
      setResult([
        ...r.messages,
        ...r.key_dates_added.map((k) => `+ deadline: ${k}`),
        ...r.key_dates_moved.map((k) => `~ deadline moved: ${k}`),
      ]);
      setLink("");
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(url: string) {
    setError(null);
    try {
      await api(`/goals/${goalId}/sources`, { method: "DELETE", query: { url } });
      await load();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const totalLeft = (sources ?? []).reduce((s, v) => s + v.load.study_minutes, 0);

  return (
    <section aria-labelledby="courses-h">
      <div className="mb-4 flex items-baseline justify-between gap-4">
        <h2 id="courses-h" className="text-lg font-semibold tracking-tight">Your courses</h2>
        {!!sources?.length && <span className="num text-sm text-muted">{minutesToHours(totalLeft)} of work left</span>}
      </div>

      {sources === null ? <Loading /> : sources.length === 0 ? (
        <p className="mb-4 text-sm text-muted">
          No courses yet. Add them and the plan uses their real length and deadlines instead of a guess.
        </p>
      ) : (
        <div className="mb-5 space-y-3">
          {sources.map((s) => <CourseCard key={s.load.url} view={s} onRemove={() => remove(s.load.url)} />)}
        </div>
      )}

      <Card>
        <Label hint="YouTube playlist or a public course page">Add by link</Label>
        <div className="flex gap-2">
          <Input value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://www.youtube.com/playlist?list=…"
                 onKeyDown={(e) => e.key === "Enter" && add()} disabled={busy} />
          <Button onClick={add} disabled={busy || !link.trim()}>{busy ? "Reading…" : "Add"}</Button>
        </div>
        <p className="mt-3 text-xs text-muted">
          Behind a login (IITM, Udemy, Coursera, LeetCode…)? Open the course page and click the Planly Chrome
          extension. It reads the page with your own login and sends it here.
        </p>
        {result && <pre className="mt-4 whitespace-pre-wrap rounded-md bg-soft p-3 text-xs leading-relaxed">{result.join("\n")}</pre>}
      </Card>
      <ErrorNote error={error} />
    </section>
  );
}

function CourseCard({ view, onRemove }: { view: SourceView; onRemove: () => void }) {
  const [open, setOpen] = useState(false);
  const { load } = view;
  const kinds = load.by_kind.filter((k) => k.remaining);
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <a href={load.url} target="_blank" rel="noreferrer" className="block truncate font-medium hover:underline">{load.title}</a>
          <p className="num mt-0.5 text-xs text-muted">
            {load.platform} · {load.done}/{load.items} done · synced {day(localDay(view.synced_at))}
          </p>
        </div>
        <span className="num shrink-0 text-lg font-semibold">{minutesToHours(load.study_minutes)}</span>
      </div>

      {kinds.length > 0 && (
        <ul className="num mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {kinds.map((k) => (
            <li key={k.kind}>
              {k.remaining} {kindWord(k.kind, k.remaining)}
              <span className="text-muted">
                {k.shown_minutes ? ` · ${minutesToHours(k.shown_minutes)} long` : ""}
                {k.study_minutes ? ` → ${minutesToHours(k.study_minutes)}` : " · size unknown"}
              </span>
            </li>
          ))}
        </ul>
      )}

      {load.due.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {load.due.slice(0, 3).map((d) => (
            <li key={d.title + d.due}><Status state="open"><span className="num">{day(d.due)}</span> · {d.title}</Status></li>
          ))}
        </ul>
      )}
      {load.unsized.length > 0 && (
        <p className="mt-3 text-xs text-muted">
          {load.unsized.length} item{load.unsized.length > 1 ? "s" : ""} with no stated length (e.g. {load.unsized[0]}): the plan estimates these.
        </p>
      )}

      <div className="mt-4 flex gap-4">
        <Button variant="ghost" onClick={() => setOpen(!open)}>{open ? "Hide items" : `Show all ${load.items} items`}</Button>
        <Button variant="ghost" onClick={onRemove}>Remove</Button>
      </div>
      {open && <ItemList items={view.items} />}
    </Card>
  );
}

function ItemList({ items }: { items: SourceItem[] }) {
  const groups: [string, SourceItem[]][] = [];
  for (const it of items) {
    const name = it.section ?? "";
    const last = groups[groups.length - 1];
    if (last && last[0] === name) last[1].push(it);
    else groups.push([name, [it]]);
  }
  return (
    <div className="mt-4 space-y-4 border-t border-line pt-4">
      {groups.map(([section, list], gi) => (
        <div key={section + gi}>
          {section && <p className="mb-1.5 text-xs font-medium uppercase tracking-wider text-muted">{section}</p>}
          <ul className="space-y-1">
            {list.map((it, i) => (
              <li key={i} className="flex items-baseline justify-between gap-3 text-sm">
                <Status state={it.done ? "ok" : "open"}>
                  <span className={it.done ? "text-muted line-through decoration-line" : ""}>{it.title}</span>
                </Status>
                <span className="num shrink-0 text-xs text-muted">
                  {[it.minutes ? `${it.minutes} min` : it.difficulty, it.due ? `due ${day(it.due)}` : null]
                    .filter(Boolean).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
