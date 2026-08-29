/** Render the JSON produced by the Python CLI (`monthly_totals` / `top_categories`) as text or HTML. */
import { formatCents, formatMonth, pad } from "./format";

export interface MonthTotals { in: number; out: number; net: number }
export interface Report {
  months: Record<string, MonthTotals>;
  topCategories: [string, number][];
}

export function renderText(report: Report): string {
  const lines = [pad("month", 10) + pad("in", 12, "right") + pad("out", 12, "right") + pad("net", 12, "right")];
  for (const [key, t] of Object.entries(report.months)) {
    lines.push(pad(formatMonth(key), 10) + pad(formatCents(t.in), 12, "right") + pad(formatCents(t.out), 12, "right") + pad(formatCents(t.net), 12, "right"));
  }
  lines.push("", "top categories");
  for (const [category, cents] of report.topCategories) lines.push("  " + pad(category, 16) + pad(formatCents(cents), 12, "right"));
  return lines.join("\n");
}

export function renderHtml(report: Report): string {
  const rows = Object.entries(report.months)
    .map(([key, t]) => `<tr><td>${formatMonth(key)}</td><td>${formatCents(t.in)}</td><td>${formatCents(t.out)}</td><td class="${t.net < 0 ? "neg" : "pos"}">${formatCents(t.net)}</td></tr>`)
    .join("");
  const cats = report.topCategories.map(([c, cents]) => `<li>${c}: ${formatCents(cents)}</li>`).join("");
  return `<table><tr><th>month</th><th>in</th><th>out</th><th>net</th></tr>${rows}</table><ul>${cats}</ul>`;
}
