/**
 * Decoder for the packed JSON written by scripts/80_export_web_data.py.
 *
 * Tables are stored column-wise, and text columns whose values repeat are stored as a dictionary plus
 * integer codes. That is what keeps the whole export at 4.35 MB instead of 23.9 MB.
 */
export type PackedColumn = (number | string | boolean | null)[] | { dict: string[]; codes: (number | null)[] };
export interface PackedTable { rows: number; columns: Record<string, PackedColumn> }

export function unpack<T = Record<string, unknown>>(table: PackedTable): T[] {
  const columns: Record<string, unknown[]> = {};
  for (const [name, column] of Object.entries(table.columns)) {
    columns[name] = Array.isArray(column)
      ? column
      : column.codes.map((code) => (code === null ? null : column.dict[code]));
  }
  const names = Object.keys(columns);
  const out: T[] = new Array(table.rows);
  for (let i = 0; i < table.rows; i++) {
    const row: Record<string, unknown> = {};
    for (const name of names) row[name] = columns[name][i];
    out[i] = row as T;
  }
  return out;
}
