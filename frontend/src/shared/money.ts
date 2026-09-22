/**
 * Money is a decimal string on the wire, never a float (ADR: money.py, 0.1).
 * This is display formatting only — nothing here does arithmetic, because
 * arithmetic on money belongs to the server's integer minor-unit math.
 */

const FORMATTERS = new Map<string, Intl.NumberFormat>();

function formatterFor(currency: string): Intl.NumberFormat {
  let formatter = FORMATTERS.get(currency);
  if (!formatter) {
    formatter = new Intl.NumberFormat("en-SG", { style: "currency", currency });
    FORMATTERS.set(currency, formatter);
  }
  return formatter;
}

export function formatMoney(amount: string, currency: string): string {
  const value = Number(amount);
  if (Number.isNaN(value)) return `${amount} ${currency}`;
  try {
    return formatterFor(currency).format(value);
  } catch {
    // An unrecognised currency code still has to render something.
    return `${amount} ${currency}`;
  }
}

export function isNegative(amount: string): boolean {
  return amount.trim().startsWith("-");
}

export function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-SG", { year: "numeric", month: "short", day: "numeric" });
}

export function formatMonth(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-SG", { year: "numeric", month: "long" });
}
