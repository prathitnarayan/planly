"use client";

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, TextareaHTMLAttributes } from "react";

export function Button({
  variant = "solid",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "solid" | "outline" | "ghost" }) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-md px-4 h-10 text-sm font-medium transition-opacity disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer";
  const look = {
    solid: "bg-ink text-paper hover:opacity-85",
    outline: "border border-ink text-ink hover:bg-soft",
    ghost: "text-muted hover:text-ink underline-offset-4 hover:underline px-0 h-auto",
  }[variant];
  return <button className={`${base} ${look} ${className}`} {...props} />;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`h-10 ${/(^|\s)!?w-/.test(props.className ?? "") ? "" : "w-full"} rounded-md border border-line bg-paper px-3 text-sm placeholder:text-muted focus:border-ink focus:outline-none ${props.className ?? ""}`}
    />
  );
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-md border border-line bg-paper p-3 text-sm leading-relaxed placeholder:text-muted focus:border-ink focus:outline-none ${props.className ?? ""}`}
    />
  );
}

export function Label({ children, hint }: { children: ReactNode; hint?: string }) {
  return (
    <span className="mb-1.5 block text-xs font-medium uppercase tracking-wider text-muted">
      {children}
      {hint && <span className="ml-2 normal-case tracking-normal">{hint}</span>}
    </span>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-lg border border-line p-5 ${className}`}>{children}</section>;
}

export function PageTitle({ kicker, children, sub }: { kicker?: string; children: ReactNode; sub?: ReactNode }) {
  return (
    <header className="mb-8">
      {kicker && <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted">{kicker}</p>}
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{children}</h1>
      {sub && <p className="mt-2 text-sm text-muted">{sub}</p>}
    </header>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p role="alert" className="mt-4 rounded-md border border-ink px-3 py-2 text-sm">
      <strong className="mr-1">!</strong>
      {error}
    </p>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted">
      <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-muted border-t-transparent" />
      {label}…
    </p>
  );
}

/** Filled dot = done / on track, hollow = open, ring with bang = late. No colour needed. */
export function Status({ state, children }: { state: "ok" | "open" | "late"; children?: ReactNode }) {
  const mark = { ok: "✓", open: "○", late: "!" }[state];
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm ${state === "late" ? "font-semibold" : state === "open" ? "text-muted" : ""}`}>
      <span aria-hidden className={`num inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] ${state === "late" ? "bg-ink text-paper" : state === "ok" ? "border border-ink" : "border border-line"}`}>
        {mark}
      </span>
      {children}
    </span>
  );
}

export function Rule() {
  return <hr className="my-8 border-line" />;
}
