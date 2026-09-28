// Dates arrive as "2026-10-15". Parse as LOCAL dates (new Date("2026-10-15") would be UTC midnight).
export function parseDay(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MO = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Tue 29 Sep" — same format everywhere, whatever the browser's locale. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseDay(iso);
  return `${WD[d.getDay()]} ${String(d.getDate()).padStart(2, "0")} ${MO[d.getMonth()]}`;
}

export function isoToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export const hours = (h: number) => `${Math.round(h * 10) / 10}h`;
export const minutesToHours = (m: number) => hours(m / 60);
export const time = (t: string | null) => (t ? t.slice(0, 5) : "");

/** "peer_review" -> "peer review" */
export const words = (key: string) => key.replace(/_/g, " ");

/** "assignment_2" -> "Assignment 2" */
export const pretty = (key: string | null) =>
  key ? key.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()) : "";

/** Drop a leading "Assignment 2:" when the group already says it. */
export function shortName(name: string, deliverable: string | null): string {
  const n = deliverable?.match(/(\d+)$/)?.[1];
  if (!n) return name;
  const stripped = name.replace(new RegExp(`^(assignment|a|task|project|module)\\s*${n}\\b[\\s:\\-–·]*`, "i"), "");
  return stripped ? stripped[0].toUpperCase() + stripped.slice(1) : name;
}
