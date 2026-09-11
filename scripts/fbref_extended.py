r"""
An FBref reader that can fetch the advanced stat pages soccerdata 1.9.1 blocks.

WHY THIS FILE EXISTS
--------------------
soccerdata's FBref.read_player_season_stats() only allows five stat types:
    standard, keeper, shooting, playing_time, misc

Our scoring approach needs passing, defense and possession as well -- they are
where progressive passes, tackles, interceptions and carries live. Without them
the position-adjusted scoring in the scoping doc (section 4.4) cannot be built.

The good news: the block is cosmetic. Look at the library's own code and the
URL it builds for any stat type outside the three special cases is simply:

    https://fbref.com/en/comps/Big5/<season>/<stat_type>/players/<slug>

...and the table it looks for is id="stats_<stat_type>". Those patterns are
already correct for passing, defense, possession, gca and passing_types. The
ONLY thing stopping us is a hardcoded list in the middle of the method.

So this subclass re-implements that one method with a longer list. It reuses
the library's own parsing helpers, so we are not writing a new scraper -- we
are removing a guardrail that is stricter than it needs to be.

RISK NOTE: we import a few underscore-prefixed helpers from soccerdata. Those
are private, so a future library version could rename them. That is an accepted,
documented trade-off, and it is pinned in requirements.txt at 1.9.1.
"""

import pandas as pd
from lxml import etree, html

import soccerdata as sd
from soccerdata._common import standardize_colnames
from soccerdata._config import TEAMNAME_REPLACEMENTS
from soccerdata.fbref import BIG_FIVE_DICT, FBREF_API, _concat, _fix_nation_col, _parse_table

# Stat types FBref publishes as season-long player tables.
# The first five are what soccerdata allows; the rest are what we are adding.
PLAYER_STAT_TYPES = [
    "standard",
    "keeper",
    "shooting",
    "playing_time",
    "misc",
    # --- added ---
    "passing",        # includes progressive passes, pass completion by distance
    "passing_types",  # crosses, switches, through balls
    "defense",        # tackles, interceptions, blocks
    "possession",     # touches by zone, carries, progressive carries, take-ons
    "gca",            # shot- and goal-creating actions (scoping doc 4.4)
    "keeper_adv",     # advanced goalkeeping
]

# stat_type -> URL path segment, where they differ.
_PAGE_OVERRIDES = {
    "standard": "stats",
    "playing_time": "playingtime",
    "keeper": "keepers",
    "keeper_adv": "keepersadv",
}


class FBrefExtended(sd.FBref):
    """FBref reader with the full set of season-long player stat pages."""

    def read_player_season_stats(self, stat_type: str = "standard") -> pd.DataFrame:
        """Same contract as the parent method, but accepts PLAYER_STAT_TYPES."""
        if stat_type not in PLAYER_STAT_TYPES:
            raise TypeError(f"Invalid stat_type {stat_type!r}; expected one of {PLAYER_STAT_TYPES}")

        page = _PAGE_OVERRIDES.get(stat_type, stat_type)
        filemask = "players_{}_{}_{}.html"

        seasons = self.read_seasons()

        players = []
        for (lkey, skey), season in seasons.iterrows():
            big_five = lkey == "Big 5 European Leagues Combined"
            filepath = self.data_dir / filemask.format(lkey, skey, stat_type)
            url = (
                FBREF_API
                + "/".join(season.url.split("/")[:-1])
                + f"/{page}"
                + ("/players/" if big_five else "/")
                + season.url.split("/")[-1]
            )
            reader = self.get(url, filepath)
            tree = html.parse(reader)
            for elem in tree.xpath("//td[@data-stat='comp_level']//span"):
                elem.getparent().remove(elem)

            if big_five:
                (html_table,) = tree.xpath(f"//table[@id='stats_{stat_type}']")
                df_table = _parse_table(html_table)
                df_table[("Unnamed: league", "league")] = (
                    df_table.xs("Comp", axis=1, level=1).squeeze().map(BIG_FIVE_DICT)
                )
                df_table[("Unnamed: season", "season")] = skey
                df_table.drop("Comp", axis=1, level=1, inplace=True)
            else:
                # On single-league pages FBref hides the table inside an HTML comment.
                (el,) = tree.xpath(f"//comment()[contains(.,'div_stats_{stat_type}')]")
                parser = etree.HTMLParser(recover=True)
                (html_table,) = etree.fromstring(el.text, parser).xpath(
                    f"//table[contains(@id, 'stats_{stat_type}')]"
                )
                df_table = _parse_table(html_table)
                df_table[("Unnamed: league", "league")] = lkey
                df_table[("Unnamed: season", "season")] = skey

            df_table = _fix_nation_col(df_table)
            players.append(df_table)

        df = _concat(players, key=["league", "season"])
        df = df[df.Player != "Player"]
        return (
            df.drop("Matches", axis=1, level=0)
            .drop("Rk", axis=1, level=0)
            .rename(columns={"Squad": "team"})
            .replace({"team": TEAMNAME_REPLACEMENTS})
            .pipe(standardize_colnames, cols=["Player", "Nation", "Pos", "Age", "Born"])
            .set_index(["league", "season", "team", "player"])
            .sort_index()
        )

    def read_team_season_stats(
        self, stat_type: str = "standard", opponent_stats: bool = False
    ) -> pd.DataFrame:
        """Same contract as the parent method, but accepts the advanced stat pages.

        Mirrors soccerdata's own implementation exactly -- the only change is the
        allowlist. Note FBref suffixes the *table id* with _for / _against while
        the URL uses the bare page name, which is why the two are tracked apart.
        """
        if stat_type not in PLAYER_STAT_TYPES:
            raise ValueError(f"Invalid stat_type {stat_type!r}; expected one of {PLAYER_STAT_TYPES}")

        page = _PAGE_OVERRIDES.get(stat_type, stat_type)
        filemask = "teams_{}_{}_{}.html"
        table_key = stat_type + ("_against" if opponent_stats else "_for")

        seasons = self.read_seasons()

        teams = []
        for (lkey, skey), season in seasons.iterrows():
            big_five = lkey == "Big 5 European Leagues Combined"
            tournament = season["format"] == "elimination"
            filepath = self.data_dir / filemask.format(lkey, skey, table_key if big_five else page)
            url = (
                FBREF_API
                + "/".join(season.url.split("/")[:-1])
                + (f"/{page}/squads/" if big_five else f"/{page}/" if tournament else "/")
                + season.url.split("/")[-1]
            )
            reader = self.get(url, filepath)

            tree = html.parse(reader)
            (html_table,) = tree.xpath(
                f"//table[@id='stats_teams_{table_key}' or @id='stats_squads_{table_key}']"
            )
            df_table = _parse_table(html_table)
            df_table["league"] = lkey
            df_table["season"] = skey
            df_table["url"] = html_table.xpath(".//*[@data-stat='team']/a/@href")
            if big_five:
                df_table["league"] = (
                    df_table.xs("Comp", axis=1, level=1).squeeze().map(BIG_FIVE_DICT)
                )
                df_table.drop("Comp", axis=1, level=1, inplace=True)
                df_table.drop("Rk", axis=1, level=1, inplace=True)
            teams.append(df_table)

        return (
            _concat(teams, key=["league", "season"])
            .rename(columns={"Squad": "team", "# Pl": "players_used"})
            .replace({"team": TEAMNAME_REPLACEMENTS})
            .set_index(["league", "season", "team"])
            .sort_index()
        )


class FBrefCached(FBrefExtended):
    """Reads ONLY from soccerdata's local HTML cache. Never starts Chrome.

    soccerdata's readers launch a browser the moment they are created, even when
    every page they need is already cached. This subclass skips that, and refuses
    to fetch anything: if a page isn't cached you get a clear FileNotFoundError
    instead of a Chrome window. Pages get into the cache via
    04_fetch_fbref_cdp.py, which uses a different browser driver (see there).
    """

    def _init_webdriver(self):
        return None

    def get(self, url, filepath=None, max_age=None, no_cache=False, var=None):
        if filepath is None or not filepath.exists():
            raise FileNotFoundError(f"Not in cache (offline reader): {filepath}\n  source page: {url}")
        return filepath.open(mode="rb")


def player_page_url(stat_type: str, season: str) -> str:
    """FBref's Big 5 combined player page, e.g. ('gca', '2324') ->
    https://fbref.com/en/comps/Big5/2023-2024/gca/players/2023-2024-Big-5-European-Leagues-Stats

    Same pattern soccerdata builds from its seasons index (read_player_season_stats above).
    """
    years = f"20{season[:2]}-20{season[2:]}"
    page = _PAGE_OVERRIDES.get(stat_type, stat_type)
    return f"{FBREF_API}/en/comps/Big5/{years}/{page}/players/{years}-Big-5-European-Leagues-Stats"


def player_cache_path(data_dir, stat_type: str, season: str):
    """Where soccerdata looks for that page, so FBrefCached can parse it."""
    return data_dir / f"players_Big 5 European Leagues Combined_{season}_{stat_type}.html"


# Columns that prove a page carries the advanced data we need, per stat type.
KEY_COLUMNS = {
    "standard": ["xg", "xag", "prgp"],
    "shooting": ["xg", "npxg"],
    "passing": ["prgp", "xag", "kp"],
    "playing_time": ["min", "mp"],
    "defense": ["tkl", "int", "clr"],
    "possession": ["prgc", "touches", "succ"],
    "misc": ["aerial", "won"],
    "gca": ["sca", "gca"],
    "keeper": ["saves", "save%"],
    "keeper_adv": ["psxg"],
}


def flatten_columns(cols) -> list[str]:
    if isinstance(cols, pd.MultiIndex):
        return ["_".join(str(p) for p in tup if "Unnamed" not in str(p)).strip("_") for tup in cols]
    return [str(c) for c in cols]


def check_player_table(df: pd.DataFrame, stat_type: str) -> tuple[bool, list[str]]:
    """Is this a usable, complete season? Returns (ok, report lines)."""
    flat = flatten_columns(df.columns)
    lines, ok = [f"rows: {len(df)}"], len(df) > 1500
    for marker in KEY_COLUMNS.get(stat_type, []):
        hits = [i for i, c in enumerate(flat) if marker in c.lower()]
        if not hits:
            lines.append(f"[MISSING] no column matching {marker!r}")
            ok = False
            continue
        s = pd.to_numeric(df.iloc[:, hits[0]], errors="coerce")
        pct = s.notna().mean() * 100 if len(df) else 0
        if pct == 0:
            label, note = "EMPTY", " -- column exists but every cell is blank in FBref's HTML"
        elif pct <= 90:
            label, note = "LOW  ", " -- partly blank"
        else:
            label, note = "OK   ", ""
        lines.append(f"[{label}] {marker!r} -> {flat[hits[0]]}: {pct:.1f}% populated{note}")
        ok = ok and pct > 90
    nineties = [i for i, c in enumerate(flat) if c.endswith("90s")]
    if nineties:
        top = pd.to_numeric(df.iloc[:, nineties[0]], errors="coerce").max()
        lines.append(f"most 90s played by any player: {top} (complete season is about 34-38)")
        ok = ok and top >= 30
    return ok, lines
