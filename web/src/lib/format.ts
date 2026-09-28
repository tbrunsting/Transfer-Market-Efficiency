const EUR = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

/** Money the way the dashboard reads it: €1.5B, €328.5M, €450k. */
export function money(eur: number | null | undefined): string {
  if (eur === null || eur === undefined) return "—";
  const abs = Math.abs(eur);
  if (abs >= 1e9) return `€${(eur / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `€${(eur / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `€${Math.round(eur / 1e3)}k`;
  return `€${EUR.format(eur)}`;
}

export function signedMoney(eur: number): string {
  return `${eur > 0 ? "+" : eur < 0 ? "−" : ""}${money(Math.abs(eur))}`;
}

export const score = (v: number): string => v.toFixed(1);
export const index = (v: number): string => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2);
export const perMatch = (v: number): string => v.toFixed(2);
