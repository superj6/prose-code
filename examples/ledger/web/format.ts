/** Formatting helpers shared by the report renderer. Amounts are integer cents. */

export function formatCents(cents: number, currency = "$"): string {
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const whole = Math.floor(abs / 100).toLocaleString("en-US");
  return `${sign}${currency}${whole}.${String(abs % 100).padStart(2, "0")}`;
}

export function formatMonth(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleString("en-US", { month: "short", year: "numeric" });
}

export function pad(text: string, width: number, align: "left" | "right" = "left"): string {
  if (text.length >= width) return text;
  const fill = " ".repeat(width - text.length);
  return align === "left" ? text + fill : fill + text;
}
