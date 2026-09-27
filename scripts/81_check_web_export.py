r"""Verify the static export against the warehouse: the site must not drift from the views.

Every table is decoded back out of its packed form and compared row by row with the view it came from,
allowing only the rounding the export applies (whole euros, 3 decimal places elsewhere). The manifest's
checksums are re-read from disk, and the ranking invariant from the presentation layer is re-checked on
the exported numbers, because rounding could in principle create ties that reorder clubs.

    .venv\Scripts\python scripts\81_check_web_export.py
"""

import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
WEB = REPO / "web" / "public"
DATA, CLUBS, CRESTS = WEB / "data", WEB / "data" / "clubs", WEB / "crests"
MONEY_TOLERANCE = 0.5        # money is rounded to whole euros
FLOAT_TOLERANCE = 5e-7       # everything else to 6 decimal places
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"<svg", b"<?xm", b"RIFF")

results: list[tuple[str, str, str]] = []


def check(name: str, expected, actual) -> None:
    results.append((name, str(expected), str(actual)))


def unpack(table: dict) -> list[dict]:
    """The decoder the TypeScript client will mirror: columns, with dictionary-coded text."""
    columns = {}
    for name, col in table["columns"].items():
        if isinstance(col, dict):
            keys, codes = col["dict"], col["codes"]
            columns[name] = [None if c is None else keys[c] for c in codes]
        else:
            columns[name] = col
    return [{name: values[i] for name, values in columns.items()} for i in range(table["rows"])]


def compare(label: str, exported: list[dict], rows: list[tuple], columns: list[str]) -> None:
    """Row-for-row equality with the view, within the rounding the export applies."""
    if len(exported) != len(rows):
        check(f"{label}: row count", len(rows), len(exported))
        return
    mismatches = 0
    for got, want in zip(exported, rows):
        for i, col in enumerate(columns):
            a, b = got.get(col), want[i]
            if b is None or a is None:
                mismatches += (a is not None) or (b is not None)
                continue
            if isinstance(b, Decimal):
                b = float(b)
            if isinstance(b, (int, float)) and not isinstance(b, bool):
                tol = MONEY_TOLERANCE if col.endswith("_eur") else FLOAT_TOLERANCE
                mismatches += abs(float(a) - float(b)) > tol
            elif hasattr(b, "isoformat"):
                mismatches += a != b.isoformat()
            else:
                mismatches += a != b
    check(f"{label}: {len(rows):,} rows match the view exactly (cells that differ)", 0, mismatches)


def main() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf8"))
    page1 = json.loads((DATA / "page1.json").read_text(encoding="utf8"))
    index = json.loads((CLUBS / "index.json").read_text(encoding="utf8"))

    # ---- the manifest describes what is actually on disk -------------------------------------------
    bad = 0
    for rel, meta in manifest["files"].items():
        path = WEB / rel
        if not path.exists():
            bad += 1
            continue
        raw = path.read_bytes()
        bad += len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != meta["sha256"]
    check("manifest: every file present with the recorded size and checksum (files that differ)", 0, bad)
    check("manifest: generated_at matches the exported payloads", True,
          page1["generated_at"] == manifest["generated_at"] == index["generated_at"])

    with psycopg.connect(os.environ.get("DATABASE_URL", "")) as conn:
        def view(sql, params=()):
            cur = conn.execute(sql, params)
            return [d.name for d in cur.description], cur.fetchall()

        cols, rows = view("""
            SELECT club_key, club_name, club_country, league, league_code, season, season_end_year,
                   gross_spend_eur, gross_sales_eur, net_spend_eur, undisclosed_signings, undisclosed_sales,
                   squad_value_start_eur, matches, points, points_per_match, has_known_deduction,
                   efficiency_index, efficiency_score_0_100, recruitment_z, trading_z, value_growth_z,
                   sporting_z, is_recruitment_scored, is_provisional
            FROM presentation.vw_league_overview ORDER BY season_end_year, club_name, club_key""")
        overview = unpack(page1["overview"])
        compare("page 1 overview", overview, rows, cols)

        cols, rows = view("""
            SELECT league, league_code, season, season_end_year, clubs, gross_spend_eur, gross_sales_eur,
                   net_spend_eur, undisclosed_signings
            FROM presentation.vw_club_spend_by_league ORDER BY season_end_year, league, league_code""")
        compare("page 1 spend by league", unpack(page1["spend_by_league"]), rows, cols)

        check("page 1: the composite weights travel with the data", 4, page1["weights"]["rows"])
        check("page 1: the league premium finding travels with the data", 5, page1["league_premium"]["rows"])

        # ---- the ranking invariant must survive rounding ---------------------------------------------
        by_club: dict[int, list[dict]] = {}
        for r in overview:
            by_club.setdefault(r["club_key"], []).append(r)
        means = {k: (sum(x["efficiency_index"] for x in v) / len(v),
                     sum(x["efficiency_score_0_100"] for x in v) / len(v)) for k, v in by_club.items()}
        flipped = sum(1 for a in means.values() for b in means.values() if a[0] < b[0] and a[1] > b[1])
        check("exported scores still rank clubs identically on index and 0-100 (pairs out of order)", 0, flipped)

        # ---- club shards ----------------------------------------------------------------------------
        in_scope = [r[0] for r in conn.execute("SELECT club_key FROM dim_club WHERE is_in_scope ORDER BY club_key")]
        check("clubs: one entry in the selector per in-scope club", len(in_scope), len(index["clubs"]))
        missing = [k for k in in_scope if not (CLUBS / f"{k}.json").exists()]
        check("clubs: every club has a shard", 0, len(missing))

        totals = {"squad": 0, "transfers": 0, "cashflow": 0, "honours": 0, "pillars": 0}
        cell_mismatches = 0
        for club_key in in_scope:
            shard = json.loads((CLUBS / f"{club_key}.json").read_text(encoding="utf8"))
            for part in totals:
                totals[part] += shard[part]["rows"]
            cols, rows = view("""
                SELECT transfer_key, season, season_end_year, transfer_date, date_is_estimated, player_name, direction,
                       direction_filter, transfer_category, fee_status, fee_eur, is_fee_disclosed,
                       other_club_name, other_club_in_scope, is_big_five_season
                FROM presentation.vw_transfers_detail WHERE club_key = %s
                ORDER BY fee_eur DESC NULLS LAST, season_end_year, player_name, direction, other_club_name, transfer_key""", (club_key,))
            got = unpack(shard["transfers"])
            if len(got) != len(rows):
                cell_mismatches += 1
                continue
            for g, w in zip(got, rows):
                for i, col in enumerate(cols):
                    a, b = g.get(col), w[i]
                    if b is None or a is None:
                        cell_mismatches += (a is not None) or (b is not None)
                    elif isinstance(b, (Decimal, int, float)) and not isinstance(b, bool):
                        cell_mismatches += abs(float(a) - float(b)) > (MONEY_TOLERANCE if col.endswith("_eur") else FLOAT_TOLERANCE)
                    elif hasattr(b, "isoformat"):
                        cell_mismatches += a != b.isoformat()
                    else:
                        cell_mismatches += a != b
        check("clubs: every transfer row in every shard matches the view (cells that differ)", 0, cell_mismatches)

        for part, sql in [("squad", "SELECT count(*) FROM presentation.vw_current_squad"),
                          ("transfers", "SELECT count(*) FROM presentation.vw_transfers_detail"),
                          ("cashflow", "SELECT count(*) FROM presentation.vw_cashflow_and_tenure"),
                          ("honours", "SELECT count(*) FROM presentation.vw_club_trophies_honors"),
                          ("pillars", "SELECT count(*) FROM presentation.vw_league_overview")]:
            check(f"clubs: {part} rows across all shards equal the view",
                  conn.execute(sql).fetchone()[0], totals[part])

        check("money: exported spend equals the warehouse to the euro",
              round(conn.execute("SELECT sum(gross_spend_eur) FROM presentation.vw_league_overview").fetchone()[0]),
              round(sum(r["gross_spend_eur"] for r in overview)))

    # ---- crests ---------------------------------------------------------------------------------
    crests = sorted(CRESTS.glob("*"))
    check("crests: one vendored image per in-scope club", 145, len(crests))
    check("crests: every file is a real image and not an error page", 0,
          sum(1 for p in crests if not p.read_bytes()[:4].startswith(IMAGE_MAGIC) or p.stat().st_size < 200))

    # ---- payload budget --------------------------------------------------------------------------
    import gzip
    page1_gz = len(gzip.compress((DATA / "page1.json").read_bytes(), 9))
    shard_gz = sorted(len(gzip.compress((CLUBS / f"{p.name}").read_bytes(), 9)) for p in CLUBS.glob("*.json"))
    check("payload: page 1 under 100 KB gzipped", "<=100 KB", f"{page1_gz / 1024:.0f} KB")
    check("payload: the largest club shard under 50 KB gzipped", "<=50 KB", f"{shard_gz[-1] / 1024:.0f} KB")

    width = max(len(n) for n, _, _ in results)
    failures = 0
    for name, expected, actual in results:
        ok = expected == actual
        if not ok and expected.startswith("<="):
            ok = float(actual.split()[0]) <= float(expected[2:].split()[0])
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  expected {expected:>12}  got {actual:>12}")
    print(f"\nverdicts: {len(results) - failures} PASS, {failures} FAIL")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
