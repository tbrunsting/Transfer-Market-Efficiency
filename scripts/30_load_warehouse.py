r"""
Phase 2: load the warehouse from the frozen sources.

CONTRACT
  * Refuses to run if any manifest checksum fails. The warehouse can only ever
    be built from the exact bytes recorded in Phase 1 and 2.
  * Idempotent: one transaction, TRUNCATE ... RESTART IDENTITY then reload, so
    running it twice leaves the same database and a failure leaves none.
  * Dimensions before facts; the deflator and the spell bridge are computed
    after fact_transfer lands, because both depend on it.

CONNECTION. Standard libpq environment variables (PGHOST, PGPORT, PGDATABASE,
PGUSER, PGPASSWORD) or DATABASE_URL. A .env file is read if present. No
credentials are stored in this repository.

    setx PGDATABASE transfer_market     (once, in your own shell)

USAGE
    .venv\Scripts\python scripts\30_load_warehouse.py --dry-run   # build and report, no database
    .venv\Scripts\python scripts\30_load_warehouse.py             # load

KNOWN VINTAGE ISSUE, surfaced not hidden: key_passes, passes_into_final_third,
passes_into_pen_area, passes_completed and passes_attempted come from the
passing file, whose seasons before 2022/23 are an older version of the data
(coverage rule 2; key passes measured about 1.4% low). Progressive passes and
xAG are taken from the standard file precisely to avoid this. The affected
columns are reported at the end of a dry run so the decision stays visible.
"""

import csv
import hashlib
import io
import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
FB = REPO / "data" / "raw" / "fbref"
FBC = FB / "csv" / "fb_big5_advanced_season_stats"
TM = REPO / "data" / "raw" / "transfermarkt"
KAGGLE = FB / "kaggle" / "fbref-2017-2024-v2"
REF = REPO / "reference"
PROC = REPO / "data" / "processed"

WINDOW = range(2018, 2025)                 # FBref season_end_year
RECENCY = [2025, 2026, 2027]               # shown but not scored (scoping doc 4.8)
LEAGUES = {"GB1": "Premier League", "ES1": "La Liga", "IT1": "Serie A", "L1": "Bundesliga", "FR1": "Ligue 1"}
MANIFESTS = ["fbref_snapshot_manifest.csv", "transfermarkt_snapshot_manifest.csv", "transfermarkt_pages_manifest.csv"]
dry_run = "--dry-run" in sys.argv
tables: dict[str, pd.DataFrame] = {}


def say(msg):
    print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# =============================================================================
# 0. The frozen bytes, or nothing
# =============================================================================
def verify_manifests():
    checked = bad = 0
    for name in MANIFESTS:
        m = pd.read_csv(REF / name)
        for r in m.itertuples():
            path = REPO / r.path
            if not path.exists():
                say(f"  MISSING {r.path}")
                bad += 1
                continue
            checked += 1
            if sha256(path) != r.sha256:
                say(f"  CHECKSUM MISMATCH {r.path}")
                bad += 1
    say(f"0. manifests: {checked} files verified, {bad} problem(s)")
    if bad:
        sys.exit("STOP: the files on disk are not the frozen sources. Re-run the freeze scripts before loading.")


# =============================================================================
# Helpers shared by the builders
# =============================================================================
def read_fb(name: str) -> pd.DataFrame:
    d = pd.read_csv(FBC / f"{name}.csv", low_memory=False)
    return d[d.Season_End_Year.isin(WINDOW)].copy()


def player_id(series):
    return series.str.extract(r"/players/([0-9a-f]{8})/")[0]


def decode_mixed(path: Path) -> pd.DataFrame:
    """The player mapping CSV is UTF-8 except three Windows-1252 lines."""
    out = []
    for raw in path.read_bytes().splitlines():
        try:
            out.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            out.append(raw.decode("cp1252"))
    return pd.read_csv(io.StringIO("\n".join(out)))


def season_label(end_year: int) -> str:
    return f"{end_year - 1}/{end_year % 100:02d}"


# =============================================================================
# 1. Dimensions
# =============================================================================
def build_dimensions():
    seasons = []
    for y in list(WINDOW) + RECENCY:
        seasons.append({"season_label": season_label(y), "season_end_year": y, "transfermarkt_year": y - 1,
                        "season_start_date": f"{y - 1}-07-01", "season_end_date": f"{y}-06-30",
                        "is_scored": y in WINDOW, "total_fees_eur": None, "median_fee_eur": None,
                        "fee_coverage_pct": None})
    tables["dim_season"] = pd.DataFrame(seasons)

    dates = pd.date_range("2012-01-01", "2027-06-30", freq="D")
    tables["dim_date"] = pd.DataFrame({
        "date_key": dates.strftime("%Y%m%d").astype(int), "full_date": dates.date,
        "year": dates.year, "month": dates.month, "day": dates.day,
        "season_key": [y + 1 if m >= 7 else y for y, m in zip(dates.year, dates.month)]})  # resolved to keys later

    comps = [{"source_code": c, "competition_name": n, "competition_type": "domestic_league",
              "country": {"GB1": "England", "ES1": "Spain", "IT1": "Italy", "L1": "Germany", "FR1": "France"}[c]}
             for c, n in LEAGUES.items()]
    cups = [("FAC", "FA Cup", "domestic_cup", "England"), ("EFL", "EFL Cup", "domestic_cup", "England"),
            ("CDR", "Copa del Rey", "domestic_cup", "Spain"), ("CIT", "Coppa Italia", "domestic_cup", "Italy"),
            ("DFB", "DFB-Pokal", "domestic_cup", "Germany"),
            ("CDF", "Coupe de France", "domestic_cup", "France"),
            ("CDL", "Coupe de la Ligue", "domestic_cup", "France"),
            ("CL", "Champions League", "european", None), ("EL", "Europa League", "european", None),
            ("UCOL", "Conference League", "european", None)]
    comps += [{"source_code": c, "competition_name": n, "competition_type": t, "country": co} for c, n, t, co in cups]
    tables["dim_competition"] = pd.DataFrame(comps)

    # club_metadata already carries transfermarkt_club_id, so no join is needed here.
    in_scope = pd.read_csv(REF / "club_metadata.csv")
    clubs = pd.DataFrame({"fbref_team_id": in_scope.fbref_team_id,
                          "transfermarkt_id": in_scope.transfermarkt_club_id.astype(int),
                          "club_name": in_scope.club_name, "country": in_scope.country, "city": in_scope.city,
                          "crest_url": in_scope.crest_url, "wikidata_qid": in_scope.wikidata_qid,
                          "is_in_scope": True})
    # Every other club a transfer points at, so the foreign keys resolve.
    tm_clubs = pd.read_csv(TM / "clubs.csv").set_index("club_id")
    pages = pd.read_csv(PROC / "tm_club_transfers.csv", low_memory=False)
    frozen = pd.read_csv(TM / "transfers.csv", low_memory=False)
    referenced = set(pages.other_club_id.dropna().astype(int)) | set(frozen.from_club_id.dropna().astype(int)) \
        | set(frozen.to_club_id.dropna().astype(int))
    extra = sorted(referenced - set(clubs.transfermarkt_id))
    others = pd.DataFrame({"fbref_team_id": None, "transfermarkt_id": extra,
                           "club_name": [tm_clubs.name.get(c, f"Transfermarkt club {c}") for c in extra],
                           "country": None, "city": None, "crest_url": None, "wikidata_qid": None,
                           "is_in_scope": False})
    tables["dim_club"] = pd.concat([clubs, others], ignore_index=True)

    tables["dim_position_group"] = pd.DataFrame([
        {"position_group": "GK", "fbref_positions": "GK"},
        {"position_group": "CB", "fbref_positions": "DF (centre-back by first position)"},
        {"position_group": "FB", "fbref_positions": "DF,MF / DF,FW wide defenders"},
        {"position_group": "CM", "fbref_positions": "MF"},
        {"position_group": "AM/W", "fbref_positions": "MF,FW / FW,MF"},
        {"position_group": "FW", "fbref_positions": "FW"}])

    tenures = pd.read_csv(REF / "manager_tenures.csv")
    splits = pd.read_csv(REF / "manager_name_review.csv")
    split_names = set(splits.loc[splits.decision == "split", "resolved_name"])
    source_of = dict(zip(splits.resolved_name, splits.manager_name))
    names = sorted(tenures.manager_name.dropna().unique())
    tables["dim_manager"] = pd.DataFrame([
        {"manager_name": n, "source_name": source_of.get(n, n), "was_name_split": n in split_names} for n in names])

    tables["dim_transfer_type"] = pd.DataFrame([
        {"transfer_type": "permanent_with_fee", "counts_as_signing": True, "counts_as_spend": True,
         "description": "Transfermarkt shows a fee, e.g. EUR 12.00m"},
        {"transfer_type": "loan_with_fee", "counts_as_signing": False, "counts_as_spend": True,
         "description": "labelled 'Loan fee: EUR x'; the loan fee counts as money, the player is not a signing"},
        {"transfer_type": "loan", "counts_as_signing": False, "counts_as_spend": False,
         "description": "labelled 'loan transfer', no fee"},
        {"transfer_type": "loan_return", "counts_as_signing": False, "counts_as_spend": False,
         "description": "labelled 'End of loan': the player going back to the parent club, not a signing"},
        {"transfer_type": "free", "counts_as_signing": True, "counts_as_spend": False,
         "description": "labelled 'free transfer'"},
        {"transfer_type": "undisclosed", "counts_as_signing": True, "counts_as_spend": False,
         "description": "fee shown as ? or -: counted as a move, never valued (scoping doc 7)"}])


# =============================================================================
# 2. fact_player_season -- assembled from nine snapshot files
# =============================================================================
STANDARD = {"MP_Playing": "matches_played", "Starts_Playing": "starts", "Min_Playing": "minutes",
            "Mins_Per_90_Playing": "nineties", "Gls": "goals", "Ast": "assists", "xG_Expected": "xg",
            "npxG_Expected": "npxg", "xAG_Expected": "xag", "PrgC_Progression": "progressive_carries",
            "PrgP_Progression": "progressive_passes", "PrgR_Progression": "progressive_received"}
OTHER_FILES = {
    "big5_player_shooting": {"Sh_Standard": "shots", "SoT_Standard": "shots_on_target"},
    # vintage caveat in the module docstring applies to this file before 2022/23
    "big5_player_passing": {"KP": "key_passes", "Final_Third": "passes_into_final_third",
                            "PPA": "passes_into_pen_area", "Cmp_Total": "passes_completed",
                            "Att_Total": "passes_attempted"},
    "big5_player_defense": {"Tkl_Tackles": "tackles", "TklW_Tackles": "tackles_won", "Int": "interceptions",
                            "Blocks_Blocks": "blocks", "Clr": "clearances", "Err": "errors"},
    "big5_player_possession": {"Touches_Touches": "touches", "Att Pen_Touches": "touches_att_pen_area",
                               "Att_Take": "take_ons_attempted", "Succ_Take": "take_ons_won",
                               "Carries_Carries": "carries", "Final_Third_Carries": "carries_into_final_third"},
    "big5_player_misc": {"Won_Aerial": "aerials_won", "Lost_Aerial": "aerials_lost"},
    "big5_player_keepers": {"Saves": "gk_saves", "GA": "gk_goals_against", "CS": "gk_clean_sheets"},
    "big5_player_keepers_adv": {"PSxG_Expected": "gk_psxg"},
}


def position_group(pos: str) -> str:
    """First listed FBref position decides the group; the raw value is kept alongside."""
    first = str(pos).split(",")[0].strip().upper()
    return {"GK": "GK", "DF": "CB", "MF": "CM", "FW": "FW"}.get(first, "CM")


def team_id_lookup() -> dict:
    """(season, club name) -> FBref team id. Unique within a season; 100% of player rows resolve."""
    ts = pd.read_csv(FBC / "big5_team_standard.csv", low_memory=False)
    ts = ts[(ts.Team_or_Opponent == "team") & ts.Season_End_Year.isin(WINDOW)]
    ts["team_id"] = ts.Url.str.extract(r"/squads/([0-9a-f]{8})/")[0]
    return dict(zip(zip(ts.Season_End_Year, ts.Squad), ts.team_id))


def build_player_season():
    std = read_fb("big5_player_standard")
    std["fbref_player_id"] = player_id(std.Url)
    keep = ["Season_End_Year", "Squad", "fbref_player_id", "Pos", "Age"] + list(STANDARD)
    f = std[keep].rename(columns=STANDARD).rename(columns={"Pos": "fbref_position_raw", "Age": "age"})
    for name, cols in OTHER_FILES.items():
        d = read_fb(name)
        d["fbref_player_id"] = player_id(d.Url)
        have = {k: v for k, v in cols.items() if k in d.columns}
        missing = set(cols) - set(have)
        if missing:
            sys.exit(f"STOP: {name} is missing expected columns {sorted(missing)}")
        f = f.merge(d[["Season_End_Year", "Squad", "fbref_player_id"] + list(have)].rename(columns=have),
                    on=["Season_End_Year", "Squad", "fbref_player_id"], how="left")

    # SCA and GCA come from Kaggle via the stored crosswalk, per-90 turned back into totals.
    cw = pd.read_csv(REF / "kaggle_fbref_crosswalk.csv")
    kag = pd.concat([pd.read_csv(p) for p in sorted(KAGGLE.glob("cleaned_*.csv"))], ignore_index=True)
    kag["season_end_year"] = kag.season.str[:4].astype(int) + 1
    sca_col = next((c for c in kag.columns if c.lower().startswith("shot creating")), None)
    gca_col = next((c for c in kag.columns if c.lower().startswith("goal creating")), None)
    if not sca_col:
        sys.exit("STOP: no shot-creating-actions column found in the Kaggle files")
    link = cw.merge(kag[["season_end_year", "player", "squad", sca_col] + ([gca_col] if gca_col else [])],
                    left_on=["season_end_year", "kaggle_player", "kaggle_squad"],
                    right_on=["season_end_year", "player", "squad"], how="left")
    link = link.rename(columns={"season_end_year": "Season_End_Year", "fbref_player_id": "fbref_player_id",
                                "fbref_squad": "Squad"})
    f = f.merge(link[["Season_End_Year", "Squad", "fbref_player_id", sca_col] + ([gca_col] if gca_col else [])],
                on=["Season_End_Year", "Squad", "fbref_player_id"], how="left")
    f["sca"] = (f[sca_col] * f.nineties).round()
    f["gca"] = (f[gca_col] * f.nineties).round() if gca_col else None
    f["sca_source"] = f[sca_col].notna().map({True: "kaggle", False: None})
    f = f.drop(columns=[c for c in (sca_col, gca_col) if c])

    # Blank snapshot values filled from Kaggle (reference/fbref_blank_fill.csv)
    fill = pd.read_csv(REF / "fbref_blank_fill.csv")
    filled_keys = set(zip(fill.season_end_year, fill.fbref_player_id, fill.fbref_squad))
    f["is_value_filled"] = [(y, p, s) in filled_keys
                            for y, p, s in zip(f.Season_End_Year, f.fbref_player_id, f.Squad)]

    f["position_group"] = f.fbref_position_raw.map(position_group)
    tid = team_id_lookup()
    f["fbref_team_id"] = [tid.get((y, c)) for y, c in zip(f.Season_End_Year, f.Squad)]
    if f.fbref_team_id.isna().any():
        sys.exit(f"STOP: {int(f.fbref_team_id.isna().sum())} player rows have a club name that maps to no team id")
    f["season_label"] = f.Season_End_Year.map(season_label)
    # Availability denominator (scoping doc 4.5): how many matches the club played that season.
    pts = pd.read_csv(REF / "club_season_points.csv")
    played = dict(zip(zip(pts.fbref_team_id, pts.season), pts.matches))
    f["team_matches_available"] = [played.get((t, sl)) for t, sl in zip(f.fbref_team_id, f.season_label)]
    tables["fact_player_season"] = f


def build_players():
    """Everyone the facts point at: FBref players from the snapshot, plus Transfermarkt-only players."""
    std = read_fb("big5_player_standard")
    std["fbref_player_id"] = player_id(std.Url)
    fb = (std.sort_values("Season_End_Year").drop_duplicates("fbref_player_id", keep="last")
             [["fbref_player_id", "Player", "Nation", "Born", "Pos"]]
             .rename(columns={"Player": "player_name", "Nation": "nationality", "Born": "birth_year",
                              "Pos": "primary_position"}))
    pmap = decode_mixed(FB / "worldfootballR" / "fbref-tm-player-mapping" / "fbref_to_tm_mapping.csv")
    pmap["fbref_player_id"] = pmap.UrlFBref.str.extract(r"/players/([0-9a-f]{8})/")[0]
    pmap["transfermarkt_id"] = pmap.UrlTmarkt.str.extract(r"/spieler/(\d+)")[0].astype("Int64")
    fb = fb.merge(pmap[["fbref_player_id", "transfermarkt_id"]].dropna().drop_duplicates("fbref_player_id"),
                  on="fbref_player_id", how="left")
    fb["foot"] = None

    tm_players = pd.read_csv(TM / "players.csv", low_memory=False)
    needed = set(tables["fact_transfer"].player_id.dropna().astype(int))
    have = set(fb.transfermarkt_id.dropna().astype(int))
    extra_ids = sorted(needed - have)
    extra = tm_players[tm_players.player_id.isin(extra_ids)]
    extra = pd.DataFrame({"fbref_player_id": None, "transfermarkt_id": extra.player_id.astype("Int64"),
                          "player_name": extra.name, "nationality": extra.country_of_citizenship,
                          "birth_year": pd.to_datetime(extra.date_of_birth, errors="coerce").dt.year,
                          "primary_position": extra.position, "foot": extra.foot})
    tables["dim_player"] = pd.concat([fb, extra], ignore_index=True)


# =============================================================================
# 3. Club-level facts
# =============================================================================
def build_club_facts():
    pts = pd.read_csv(REF / "club_season_points.csv")
    pts["competition_code"] = pts.league.map({v: k for k, v in LEAGUES.items()})
    tables["fact_club_season"] = pts

    tro = pd.read_csv(REF / "club_trophies_detail.csv")
    tro = tro[tro.fbref_team_id.notna()]
    code_of = {"Premier League": "GB1", "La Liga": "ES1", "Serie A": "IT1", "Bundesliga": "L1", "Ligue 1": "FR1",
               "FA Cup": "FAC", "EFL Cup": "EFL", "Copa del Rey": "CDR", "Coppa Italia": "CIT",
               "DFB-Pokal": "DFB", "Coupe de France": "CDF", "Coupe de la Ligue": "CDL",
               "Champions League": "CL", "Europa League": "EL", "Conference League": "UCOL"}
    unknown = set(tro.competition) - set(code_of)
    if unknown:
        sys.exit(f"STOP: trophies reference competitions with no code: {sorted(unknown)}")
    tro["competition_code"] = tro.competition.map(code_of)
    tables["fact_club_trophy"] = tro

    ten = pd.read_csv(REF / "manager_tenures.csv")
    tables["fact_manager_tenure"] = ten


# =============================================================================
# 4. fact_transfer -- pages primary, frozen dataset only where a page has nothing
# =============================================================================
def build_transfers():
    mapping = pd.read_csv(REF / "club_id_mapping.csv")
    ours = set(mapping.transfermarkt_club_id.astype(int))
    pages = pd.read_csv(PROC / "tm_club_transfers.csv", low_memory=False)
    pages = pages[pages.other_club_id.notna()].copy()
    pages["other_club_id"] = pages.other_club_id.astype(int)
    # A page row is one side of an event; turn both sides into one directed event.
    pages["from_club"] = pages.apply(lambda r: r.other_club_id if r.direction == "in" else r.club_id, axis=1)
    pages["to_club"] = pages.apply(lambda r: r.club_id if r.direction == "in" else r.other_club_id, axis=1)
    kind_to_type = {"fee": "permanent_with_fee", "loan_with_fee": "loan_with_fee", "loan": "loan",
                    "end_of_loan": "loan_return", "free": "free", "undisclosed": "undisclosed"}
    unknown = set(pages.fee_kind) - set(kind_to_type)
    if unknown:
        sys.exit(f"STOP: unmapped fee kinds from the pages: {sorted(unknown)}")
    pages["transfer_type"] = pages.fee_kind.map(kind_to_type)
    pages["fee_eur"] = pages.fee_eur.where(pages.fee_kind != "undisclosed")   # undisclosed stays NULL
    ev = (pages.sort_values("fee_eur", ascending=False)
                .drop_duplicates(["player_id", "season", "from_club", "to_club"])
                .rename(columns={"season": "tm_season"}))

    # Dates: inherit from the frozen dataset where the same event is there, else the season's nominal start.
    frozen = pd.read_csv(TM / "transfers.csv", low_memory=False)
    frozen["transfer_date"] = pd.to_datetime(frozen.transfer_date, errors="coerce")
    key = ["player_id", "from_club_id", "to_club_id"]
    fz = frozen.dropna(subset=["transfer_date"]).copy()
    fz["tm_season"] = fz.transfer_season.str[:2].astype(int) + 2000
    fz = fz.drop_duplicates(key + ["tm_season"])
    ev = ev.merge(fz[key + ["tm_season", "transfer_date", "market_value_in_eur"]],
                  left_on=["player_id", "from_club", "to_club", "tm_season"],
                  right_on=key + ["tm_season"], how="left")
    ev["date_is_estimated"] = ev.transfer_date.isna()
    ev["transfer_date"] = ev.transfer_date.fillna(pd.to_datetime(ev.tm_season.astype(str) + "-07-01"))
    ev["season_end_year"] = ev.tm_season + 1
    ev["source_system"] = "transfermarkt-pages"
    ev["source_ref"] = ev.club_id.astype(str) + ":" + ev.tm_season.astype(str) + ":" + ev.player_id.astype(str)
    ev["is_type_heuristic"] = False
    ev = ev[ev.from_club.isin(ours) | ev.to_club.isin(ours)]
    tables["fact_transfer"] = ev

    vals = pd.read_csv(TM / "player_valuations.csv", low_memory=False)
    tables["fact_player_valuation"] = vals


# =============================================================================
# 5. Derived after fact_transfer: the deflator and the spell bridge
# =============================================================================
def build_derived():
    ev = tables["fact_transfer"]
    mapping = pd.read_csv(REF / "club_id_mapping.csv")
    pts = pd.read_csv(REF / "club_season_points.csv")
    fb_of_tm = dict(zip(mapping.transfermarkt_club_id.astype(int), mapping.fbref_team_id))
    in_scope = {(r.fbref_team_id, r.season) for r in pts.itertuples()}

    ev["to_fbref"] = ev.to_club.map(fb_of_tm)
    ev["season_label"] = ev.season_end_year.map(season_label)
    ev["arrival_in_scope"] = [(t, s) in in_scope for t, s in zip(ev.to_fbref, ev.season_label)]
    fees = ev[(ev.transfer_type == "permanent_with_fee") & ev.arrival_in_scope]
    per_season = fees.groupby("season_end_year").fee_eur.agg(["sum", "median"])
    ds = tables["dim_season"].set_index("season_end_year")
    ds.loc[per_season.index, "total_fees_eur"] = per_season["sum"]
    ds.loc[per_season.index, "median_fee_eur"] = per_season["median"]
    tables["dim_season"] = ds.reset_index()
    totals, medians = per_season["sum"].to_dict(), per_season["median"].to_dict()
    ev["fee_share_of_season"] = [f / totals[y] if pd.notna(f) and y in totals else None
                                 for f, y in zip(ev.fee_eur, ev.season_end_year)]
    ev["fee_vs_season_median"] = [f / medians[y] if pd.notna(f) and y in medians else None
                                  for f, y in zip(ev.fee_eur, ev.season_end_year)]

    # Spells: an arrival at a club, ending at that player's next departure from it.
    moves = ev.sort_values("transfer_date")
    spells = []
    for (pid, club), grp in moves.groupby(["player_id", "to_club"]):
        for arrival in grp.itertuples():
            later = moves[(moves.player_id == pid) & (moves.from_club == club)
                          & (moves.transfer_date > arrival.transfer_date)]
            dep = later.iloc[0] if len(later) else None
            spells.append({"player_id": pid, "club_id": club,
                           "arrival_ref": arrival.source_ref,
                           "departure_ref": dep.source_ref if dep is not None else None,
                           "start_date": arrival.transfer_date,
                           "end_date": dep.transfer_date if dep is not None else None,
                           "purchase_fee_eur": arrival.fee_eur, "sale_fee_eur": dep.fee_eur if dep is not None else None,
                           "market_value_at_arrival": arrival.market_value_in_eur,
                           "arrival_known": True,
                           "departure_known": dep is not None})
    tables["bridge_player_club_spell"] = pd.DataFrame(spells)


# =============================================================================
# 6. meta
# =============================================================================
def build_meta():
    man = pd.concat([pd.read_csv(REF / m).assign(manifest=m) for m in MANIFESTS], ignore_index=True)
    tables["meta.source_manifest"] = man
    ledgers = {"club_mapping_review.csv": "club_id_mapping", "club_metadata_review.csv": "club_metadata",
               "manager_name_review.csv": "manager_tenures"}
    rows = []
    for f, src in ledgers.items():
        d = pd.read_csv(REF / f)
        for r in d.itertuples():
            rows.append({"source_table": src,
                         "entity_id": getattr(r, "fbref_team_id", None) or getattr(r, "manager_name", ""),
                         "field": getattr(r, "field", None) or getattr(r, "club", None),
                         "value": str(getattr(r, "value", "") or getattr(r, "resolved_name", "")),
                         "decision": r.decision, "decided_by": r.decided_by, "decided_on": r.decided_on,
                         "reason": r.reason})
    tables["meta.decision"] = pd.DataFrame(rows)
    tables["meta.transfer_coverage"] = pd.read_csv(REF / "transfer_coverage_check.csv")


# =============================================================================
# main
# =============================================================================
verify_manifests()
say("1. dimensions")
build_dimensions()
say("2. fact_player_season")
build_player_season()
say("3. club facts")
build_club_facts()
say("4. fact_transfer and valuations")
build_transfers()
say("4b. dim_player")
build_players()
say("5. deflator and spell bridge")
build_derived()
say("6. meta")
build_meta()

say("\nbuilt:")
for name, df in tables.items():
    say(f"  {name:<28} {len(df):>9,} rows")

ps = tables["fact_player_season"]
old_vintage = ps[ps.Season_End_Year < 2023].key_passes.notna().sum()
say(f"\nVINTAGE NOTE: {old_vintage:,} player-seasons before 2022/23 carry passing-file columns "
    "(key_passes, passes_*) from the older data version. Progressive passes and xAG come from the "
    "standard file and are unaffected.")

if dry_run:
    say("\n--dry-run: nothing was written to Postgres.")
    sys.exit(0)

# ---- load ----------------------------------------------------------------
try:
    import psycopg
    from dotenv import load_dotenv
except ImportError as e:
    sys.exit(f"STOP: {e}. Install requirements first.")
load_dotenv(REPO / ".env")
dsn = os.environ.get("DATABASE_URL") or ""
if not dsn and not os.environ.get("PGDATABASE"):
    sys.exit("STOP: set DATABASE_URL or the PG* environment variables (see this script's docstring). "
             "No credentials are stored in the repository.")


def clean(df, columns):
    """Frame -> rows of Python values, with NaN/NaT as None so Postgres sees NULL."""
    out = df.reindex(columns=columns).astype(object)
    return out.where(pd.notna(out), None)


def copy_in(cur, table, df, columns):
    rows = clean(df, columns)
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for row in rows.itertuples(index=False, name=None):
            cp.write_row(row)
    return len(rows)


def keymap(cur, table, natural, key):
    cur.execute(f"SELECT {natural}, {key} FROM {table} WHERE {natural} IS NOT NULL")
    return {k: v for k, v in cur.fetchall()}


def date_key(value):
    ts = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(ts) else int(ts.strftime("%Y%m%d"))


say(f"\nconnecting to {os.environ.get('PGDATABASE') or dsn.split('@')[-1]}")
loaded = {}
with psycopg.connect(dsn or "", autocommit=False) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.fact_transfer')")
        if cur.fetchone()[0] is None:
            sys.exit("STOP: the schema does not exist. Run sql/01_schema.sql first.")

        say("truncating (one transaction: a failure leaves the warehouse untouched)")
        cur.execute("""TRUNCATE meta.transfer_coverage, meta.decision, meta.source_manifest,
                                bridge_player_club_spell, fact_player_valuation, fact_manager_tenure,
                                fact_club_trophy, fact_club_season, fact_player_season, fact_transfer,
                                dim_transfer_type, dim_manager, dim_position_group, dim_player, dim_club,
                                dim_competition, dim_date, dim_season
                       RESTART IDENTITY CASCADE""")

        # ---------------- dimensions
        loaded["dim_season"] = copy_in(cur, "dim_season", tables["dim_season"],
            ["season_label", "season_end_year", "transfermarkt_year", "season_start_date", "season_end_date",
             "is_scored", "total_fees_eur", "median_fee_eur"])
        season_key = keymap(cur, "dim_season", "season_label", "season_key")
        season_key_by_year = keymap(cur, "dim_season", "season_end_year", "season_key")

        dd = tables["dim_date"].copy()
        dd["season_key"] = dd.season_key.map(season_key_by_year)   # was the season end year
        loaded["dim_date"] = copy_in(cur, "dim_date", dd,
                                     ["date_key", "full_date", "year", "month", "day", "season_key"])

        loaded["dim_competition"] = copy_in(cur, "dim_competition", tables["dim_competition"],
            ["source_code", "competition_name", "competition_type", "country"])
        competition_key = keymap(cur, "dim_competition", "source_code", "competition_key")

        loaded["dim_club"] = copy_in(cur, "dim_club", tables["dim_club"],
            ["fbref_team_id", "transfermarkt_id", "club_name", "country", "city", "crest_url",
             "wikidata_qid", "is_in_scope"])
        club_by_fbref = keymap(cur, "dim_club", "fbref_team_id", "club_key")
        club_by_tm = keymap(cur, "dim_club", "transfermarkt_id", "club_key")

        loaded["dim_player"] = copy_in(cur, "dim_player", tables["dim_player"],
            ["fbref_player_id", "transfermarkt_id", "player_name", "nationality", "birth_year",
             "primary_position", "foot"])
        player_by_fbref = keymap(cur, "dim_player", "fbref_player_id", "player_key")
        player_by_tm = keymap(cur, "dim_player", "transfermarkt_id", "player_key")

        loaded["dim_position_group"] = copy_in(cur, "dim_position_group", tables["dim_position_group"],
                                               ["position_group", "fbref_positions"])
        position_key = keymap(cur, "dim_position_group", "position_group", "position_group_key")

        loaded["dim_manager"] = copy_in(cur, "dim_manager", tables["dim_manager"],
                                        ["manager_name", "source_name", "was_name_split"])
        manager_key = keymap(cur, "dim_manager", "manager_name", "manager_key")

        loaded["dim_transfer_type"] = copy_in(cur, "dim_transfer_type", tables["dim_transfer_type"],
            ["transfer_type", "counts_as_signing", "counts_as_spend", "description"])
        type_key = keymap(cur, "dim_transfer_type", "transfer_type", "transfer_type_key")

        # ---------------- facts
        ps = tables["fact_player_season"].copy()
        ps["player_key"] = ps.fbref_player_id.map(player_by_fbref)
        ps["club_key"] = ps.fbref_team_id.map(club_by_fbref)
        ps["season_key"] = ps.season_label.map(season_key)
        ps["position_group_key"] = ps.position_group.map(position_key)
        loaded["fact_player_season"] = copy_in(cur, "fact_player_season", ps,
            ["player_key", "club_key", "season_key", "position_group_key", "fbref_position_raw", "age",
             "matches_played", "starts", "minutes", "nineties", "team_matches_available", "goals", "assists",
             "xg", "npxg", "xag", "shots", "shots_on_target", "progressive_passes", "progressive_carries",
             "progressive_received", "key_passes", "passes_into_final_third", "passes_into_pen_area",
             "passes_completed", "passes_attempted", "tackles", "tackles_won", "interceptions", "blocks",
             "clearances", "errors", "aerials_won", "aerials_lost", "touches", "touches_att_pen_area",
             "take_ons_attempted", "take_ons_won", "carries", "carries_into_final_third", "sca", "gca",
             "sca_source", "gk_saves", "gk_goals_against", "gk_psxg", "gk_clean_sheets", "is_value_filled"])

        cs = tables["fact_club_season"].copy()
        cs["club_key"] = cs.fbref_team_id.map(club_by_fbref)
        cs["season_key"] = cs.season.map(season_key)
        cs["competition_key"] = cs.competition_code.map(competition_key)
        cs = cs.rename(columns={"position_transfermarkt": "position_source",
                                "known_deduction": "has_known_deduction", "notes": "deduction_note"})
        cs["has_known_deduction"] = cs.has_known_deduction.eq("yes")
        cs["deduction_note"] = cs.deduction_note.where(cs.has_known_deduction)
        loaded["fact_club_season"] = copy_in(cur, "fact_club_season", cs,
            ["club_key", "season_key", "competition_key", "matches", "wins", "draws", "losses", "goals_for",
             "goals_against", "goal_difference", "points_from_results", "position_computed", "position_source",
             "has_known_deduction", "deduction_note"])

        tr = tables["fact_club_trophy"].copy()
        tr["club_key"] = tr.fbref_team_id.map(club_by_fbref)
        tr["season_key"] = tr.season.map(season_key)
        tr["competition_key"] = tr.competition_code.map(competition_key)
        tr = tr.rename(columns={"category": "trophy_category", "source": "source_system"})
        loaded["fact_club_trophy"] = copy_in(cur, "fact_club_trophy", tr,
            ["club_key", "season_key", "competition_key", "trophy_category", "source_system", "source_ref"])

        mt = tables["fact_manager_tenure"].copy()
        mt["club_key"] = mt.fbref_team_id.map(club_by_fbref)
        mt["manager_key"] = mt.manager_name.map(manager_key)
        mt["first_match_date_key"] = mt.first_match_date.map(date_key)
        mt["last_match_date_key"] = mt.last_match_date.map(date_key)
        mt["first_season_key"] = mt.first_season.map(season_key)
        mt["last_season_key"] = mt.last_season.map(season_key)
        mt["is_likely_caretaker"] = mt.likely_caretaker.eq("yes")
        mt["boundaries_are_match_based"] = True
        loaded["fact_manager_tenure"] = copy_in(cur, "fact_manager_tenure", mt,
            ["club_key", "manager_key", "stint_seq", "first_match_date_key", "last_match_date_key",
             "first_season_key", "last_season_key", "matches", "league_matches", "is_likely_caretaker",
             "boundaries_are_match_based"])

        ev = tables["fact_transfer"].copy()
        ev["player_key"] = ev.player_id.map(player_by_tm)
        ev["from_club_key"] = ev.from_club.map(club_by_tm)
        ev["to_club_key"] = ev.to_club.map(club_by_tm)
        ev["transfer_date_key"] = ev.transfer_date.map(date_key)
        ev["season_key"] = ev.season_end_year.map(season_key_by_year)
        ev["transfer_type_key"] = ev.transfer_type.map(type_key)
        ev["is_fee_disclosed"] = ev.fee_eur.notna()
        ev = ev.rename(columns={"market_value_in_eur": "market_value_eur"})
        loaded["fact_transfer"] = copy_in(cur, "fact_transfer", ev,
            ["player_key", "from_club_key", "to_club_key", "transfer_date_key", "season_key",
             "transfer_type_key", "fee_eur", "is_fee_disclosed", "market_value_eur", "fee_share_of_season",
             "fee_vs_season_median", "is_type_heuristic", "date_is_estimated", "source_system", "source_ref"])
        transfer_by_ref = keymap(cur, "fact_transfer", "source_ref", "transfer_key")

        pv = tables["fact_player_valuation"].copy()
        pv["player_key"] = pv.player_id.map(player_by_tm)
        pv = pv[pv.player_key.notna()]
        pv["valuation_date_key"] = pv.date.map(date_key)
        pv = pv[pv.valuation_date_key.notna()]
        pv["club_key"] = pv.current_club_id.map(club_by_tm)
        pv = pv.rename(columns={"market_value_in_eur": "market_value_eur"})
        pv = pv.drop_duplicates(["player_key", "valuation_date_key"])
        loaded["fact_player_valuation"] = copy_in(cur, "fact_player_valuation", pv,
            ["player_key", "valuation_date_key", "club_key", "market_value_eur"])

        sp = tables["bridge_player_club_spell"].copy()
        sp["player_key"] = sp.player_id.map(player_by_tm)
        sp["club_key"] = sp.club_id.map(club_by_tm)
        sp["arrival_transfer_key"] = sp.arrival_ref.map(transfer_by_ref)
        sp["departure_transfer_key"] = sp.departure_ref.map(transfer_by_ref)
        sp["start_date_key"] = sp.start_date.map(date_key)
        sp["end_date_key"] = sp.end_date.map(date_key)
        sp["arrival_known"] = sp.arrival_transfer_key.notna()
        sp["departure_known"] = sp.departure_transfer_key.notna()
        sp["purchase_fee_eur"] = sp.purchase_fee_eur.where(sp.arrival_known)
        sp = sp[sp.player_key.notna() & sp.club_key.notna()].drop_duplicates(
            ["player_key", "club_key", "start_date_key"])
        loaded["bridge_player_club_spell"] = copy_in(cur, "bridge_player_club_spell", sp,
            ["player_key", "club_key", "arrival_transfer_key", "departure_transfer_key", "start_date_key",
             "end_date_key", "purchase_fee_eur", "sale_fee_eur", "market_value_at_arrival",
             "arrival_known", "departure_known"])

        # ---------------- meta
        loaded["meta.source_manifest"] = copy_in(cur, "meta.source_manifest", tables["meta.source_manifest"],
            ["path", "role", "origin", "version", "source_url", "source_updated_at", "size_bytes", "sha256",
             "retrieved_at", "rows", "first_season", "last_season", "notes"])
        loaded["meta.decision"] = copy_in(cur, "meta.decision", tables["meta.decision"],
            ["source_table", "entity_id", "field", "value", "decision", "decided_by", "decided_on", "reason"])
        cvg = tables["meta.transfer_coverage"].copy()
        cvg["club_key"] = cvg.club_id.map(club_by_tm)
        cvg["season_key"] = cvg.season_label.map(lambda s: season_key.get(f"20{s[:2]}/{s[-2:]}"))
        cvg = cvg[cvg.club_key.notna() & cvg.season_key.notna()]
        cvg = cvg.rename(columns={"fees_missing_from_frozen_eur": "fees_missing_eur"})
        loaded["meta.transfer_coverage"] = copy_in(cur, "meta.transfer_coverage", cvg,
            ["club_key", "season_key", "page_fees_eur", "frozen_fees_eur", "fees_missing_eur",
             "page_arrivals", "frozen_arrivals", "is_in_scope"])

    conn.commit()

say("\nloaded:")
for name, n in loaded.items():
    say(f"  {name:<28} {n:>9,} rows")
say("\ncommitted. Run sql/03_checks.sql next.")
