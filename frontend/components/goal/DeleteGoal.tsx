"use client";

import { useState } from "react";
import { api } from "@/lib/api";

/** Two-step delete: first click asks, second click deletes. Removes the plan, check-ins,
 *  ticks and courses for this goal (the database cascades). Can't be undone. */
export function DeleteGoal({ goalId, title, onDeleted, compact = false }: {
  goalId: string; title: string; onDeleted: () => void; compact?: boolean;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api(`/goals/${goalId}`, { method: "DELETE" });
      onDeleted();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (!asking) {
    return (
      <button onClick={(e) => { e.preventDefault(); e.stopPropagation(); setAsking(true); }}
              className={compact ? "cursor-pointer px-2 text-xs text-muted hover:text-ink hover:underline underline-offset-4"
                                 : "h-9 cursor-pointer rounded-full border border-line px-4 text-sm hover:border-ink"}>
        Delete{compact ? "" : " goal"}
      </button>
    );
  }
  return (
    <span className="rise inline-flex flex-wrap items-center gap-2 text-sm" onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
      <span className={compact ? "text-xs" : ""}>
        Delete <strong>{compact ? "it" : `“${title}”`}</strong>{compact ? "?" : "? Its plan, ticks, check-ins and courses go too. Can't be undone."}
      </span>
      <button onClick={remove} disabled={busy}
              className="h-8 cursor-pointer rounded-md bg-ink px-3 text-xs font-medium text-paper hover:opacity-85 disabled:opacity-40">
        {busy ? "Deleting…" : "Yes, delete"}
      </button>
      <button onClick={() => setAsking(false)} disabled={busy} className="cursor-pointer text-xs text-muted underline underline-offset-4">
        Cancel
      </button>
      {error && <span className="basis-full text-xs"><strong>!</strong> {error}</span>}
    </span>
  );
}
