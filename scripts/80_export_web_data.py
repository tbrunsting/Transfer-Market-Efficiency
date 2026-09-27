r"""Export the presentation views to static JSON for the Astro site (web/).

GitHub Pages has no database, so the site ships a snapshot. This writes it:

    web/public/data/page1.json          league overview + spend by league + meta (29 KB gzipped)
    web/public/data/clubs/index.json    the club selector
    web/public/data/clubs/<key>.json    one shard per club: squad, transfers, cash flow, honours
    web/public/crests/<key>.png         club crests, vendored rather than hotlinked
    web/public/data/manifest.json       generated-at, row counts, SHA-256 and byte size per file

Encoding. An array of objects would be 23.9 MB (1.56 MB gzipped), because every row repeats every key
name. Each table is therefore stored column-wise, and any text column whose values repeat is stored as a
dictionary plus integer codes:

    {"rows": 684, "columns": {"points": [42, 58, ...],
                              "league": {"dict": ["La Liga", ...], "codes": [1, 0, ...]}}}

That is 4.35 MB (859 KB gzipped) for everything, and the site never loads everything: page 1 reads one
113 KB file, and a club shard is a median of 36 KB.

Run from the repository root, after R/70_presentation_views.R:

    .venv\Scripts\python scripts\80_export_web_data.py
    .venv\Scripts\python scripts\81_check_web_export.py
"""

import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import requests
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
WEB = REPO / "web" / "public"
DATA, CLUBS, CRESTS = WEB / "data", WEB / "data" / "clubs", WEB / "crests"
DICTIONARY_RATIO = 3          # dictionary-encode a text column when values repeat at least this often
MONEY_SUFFIX = "_eur"
FLOAT_DP = 6            # 3 dp created a near-tie that reordered two clubs; see 81_check_web_export.py
CREST_PAUSE_SECONDS = 0.3     # be polite to Transfermarkt; 145 images, one pass


def normalise(value, money: bool):
    """Postgres types to JSON-safe values: dates as ISO text, euros as whole numbers, floats rounded."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(round(value)) if money else round(value, FLOAT_DP)
    if isinstance(value, int) or isinstance(value, bool) or isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def pack(columns: list[str], rows: list[tuple]) -> dict:
    """Column-wise, with dictionary encoding for repeated text. See the module docstring for the shape."""
    out = {}
    for i, name in enumerate(columns):
        money = name.endswith(MONEY_SUFFIX)
        values = [normalise(r[i], money) for r in rows]
        text = {v for v in values if isinstance(v, str)}
        if text and len(text) * DICTIONARY_RATIO <= len(values):
            keys = sorted(text)
            code_of = {k: j for j, k in enumerate(keys)}
            out[name] = {"dict": keys, "codes": [code_of.get(v) for v in values]}
        else:
            out[name] = values
    return {"rows": len(rows), "columns": out}


def fetch(conn, sql: str, params=()) -> dict:
    cur = conn.execute(sql, params)
    return pack([d.name for d in cur.description], cur.fetchall())


def write_json(path: Path, payload) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf8")
    path.write_bytes(raw)
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main() -> None:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    files: dict[str, dict] = {}

    with psycopg.connect(os.environ.get("DATABASE_URL", "")) as conn:
        if conn.execute("SELECT to_regclass('presentation.vw_league_overview')").fetchone()[0] is None:
            sys.exit("STOP: the presentation views are missing. Run R/70_presentation_views.R first.")

        # ---- page 1 -------------------------------------------------------------------------------
        page1 = {
            "generated_at": generated_at,
            "overview": fetch(conn, """
                SELECT club_key, club_name, club_country, league, league_code, season, season_end_year,
                       gross_spend_eur, gross_sales_eur, net_spend_eur, undisclosed_signings, undisclosed_sales,
                       squad_value_start_eur, matches, points, points_per_match, has_known_deduction,
                       efficiency_index, efficiency_score_0_100, recruitment_z, trading_z, value_growth_z,
                       sporting_z, is_recruitment_scored, is_provisional
                FROM presentation.vw_league_overview ORDER BY season_end_year, club_name, club_key"""),
            "spend_by_league": fetch(conn, """
                SELECT league, league_code, season, season_end_year, clubs, gross_spend_eur, gross_sales_eur,
                       net_spend_eur, undisclosed_signings
                FROM presentation.vw_club_spend_by_league ORDER BY season_end_year, league, league_code"""),
            "weights": fetch(conn, "SELECT pillar, source_column, weight, rationale FROM score.composite_weight ORDER BY weight DESC, pillar"),
            "league_premium": fetch(conn, """
                SELECT competition_name, reference_league, output_per_euro_vs_reference, median_output_per_spend
                FROM score.league_premium ORDER BY output_per_euro_vs_reference"""),
        }
        files["data/page1.json"] = write_json(DATA / "page1.json", page1)

        # ---- the club selector --------------------------------------------------------------------
        cur = conn.execute("""
            SELECT c.club_key, c.club_name, c.country, c.city, c.crest_url,
                   (SELECT string_agg(DISTINCT o.league, ' / ' ORDER BY o.league)
                    FROM presentation.vw_league_overview o WHERE o.club_key = c.club_key) AS leagues,
                   (SELECT count(*) FROM presentation.vw_league_overview o WHERE o.club_key = c.club_key) AS seasons
            FROM dim_club c WHERE c.is_in_scope ORDER BY c.club_name""")
        club_rows = cur.fetchall()
        index = [{"club_key": r[0], "club_name": r[1], "country": r[2], "city": r[3],
                  "leagues": r[5], "seasons": r[6]} for r in club_rows]
        files["data/clubs/index.json"] = write_json(CLUBS / "index.json",
                                                    {"generated_at": generated_at, "clubs": index})

        # ---- one shard per club --------------------------------------------------------------------
        for club_key, club_name, country, city, _crest, leagues, seasons in club_rows:
            shard = {
                "generated_at": generated_at,
                "club": {"club_key": club_key, "club_name": club_name, "country": country, "city": city,
                         "leagues": leagues, "seasons": seasons},
                "squad": fetch(conn, """
                    SELECT player_key, season, season_end_year, is_latest_season_for_club, clubs_in_season,
                           is_primary_club_for_season, player_name, nationality_code, position_group,
                           position_detail, position_short, age, matches_played, starts, minutes,
                           quality_percentile, market_value_eur
                    FROM presentation.vw_current_squad WHERE club_key = %s
                    ORDER BY season_end_year, market_value_eur DESC NULLS LAST, player_name, player_key""", (club_key,)),
                "transfers": fetch(conn, """
                    SELECT transfer_key, season, season_end_year, transfer_date, date_is_estimated, player_name, direction,
                           direction_filter, transfer_category, fee_status, fee_eur, is_fee_disclosed,
                           other_club_name, other_club_in_scope, is_big_five_season
                    FROM presentation.vw_transfers_detail WHERE club_key = %s
                    ORDER BY fee_eur DESC NULLS LAST, season_end_year, player_name, direction, other_club_name, transfer_key""", (club_key,)),
                "cashflow": fetch(conn, """
                    SELECT season, season_end_year, fees_paid_eur, fees_received_eur, net_spend_eur,
                           net_transfer_balance_eur, undisclosed_signings, undisclosed_sales, efficiency_index,
                           efficiency_score_0_100, is_provisional, season_status, manager_name,
                           manager_is_caretaker, managers_in_season, managers_count, manager_band_seq
                    FROM presentation.vw_cashflow_and_tenure WHERE club_key = %s ORDER BY season_end_year""", (club_key,)),
                "honours": fetch(conn, """
                    SELECT league_titles, domestic_cups, european_trophies, total_trophies, window_label
                    FROM presentation.vw_club_trophies_honors WHERE club_key = %s""", (club_key,)),
                "pillars": fetch(conn, """
                    SELECT season, season_end_year, efficiency_index, efficiency_score_0_100, recruitment_z,
                           trading_z, value_growth_z, sporting_z, is_recruitment_scored, is_provisional
                    FROM presentation.vw_league_overview WHERE club_key = %s ORDER BY season_end_year""", (club_key,)),
            }
            files[f"data/clubs/{club_key}.json"] = write_json(CLUBS / f"{club_key}.json", shard)

    # ---- crests, downloaded once rather than hotlinked ---------------------------------------------
    CRESTS.mkdir(parents=True, exist_ok=True)
    downloaded = skipped = failed = 0
    for club_key, club_name, _country, _city, crest_url, _leagues, _seasons in club_rows:
        if not crest_url:
            failed += 1
            continue
        suffix = ".png" if ".png" in crest_url.lower() else Path(crest_url.split("?")[0]).suffix or ".png"
        target = CRESTS / f"{club_key}{suffix}"
        if target.exists():
            skipped += 1
            continue
        try:
            r = requests.get(crest_url, timeout=30,
                             headers={"User-Agent": "transfer-market-efficiency/1.0 (portfolio project)"})
            r.raise_for_status()
            target.write_bytes(r.content)
            downloaded += 1
            time.sleep(CREST_PAUSE_SECONDS)
        except Exception as exc:                      # noqa: BLE001 - report and carry on
            print(f"  crest failed for {club_name}: {exc}")
            failed += 1
    print(f"crests: {downloaded} downloaded, {skipped} already present, {failed} missing")

    for path in sorted(CRESTS.glob("*")):
        files[f"crests/{path.name}"] = {"bytes": path.stat().st_size,
                                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    manifest = {"generated_at": generated_at, "source": "presentation.* views in the transfer_market warehouse",
                "files": files}
    write_json(DATA / "manifest.json", manifest)

    total = sum(f["bytes"] for f in files.values())
    print(f"exported {len(files)} files, {total / 1024 / 1024:.2f} MB on disk "
          f"(page 1 alone: {files['data/page1.json']['bytes'] / 1024:.0f} KB)")
    print(f"generated_at {generated_at}; manifest at web/public/data/manifest.json")
    print("next: .venv\\Scripts\\python scripts\\81_check_web_export.py")


if __name__ == "__main__":
    main()
