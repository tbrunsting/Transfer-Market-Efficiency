# Phase 1 — FBref scraper diagnostics

Status at end of day, 2026-09-11 (updated late evening after further tests).
Written so the next session starts here instead of re-deriving it.

## Resolution: the data route is decided

Most of this document records the investigation as it happened. The route that
came out of it:

- **FBref data comes from worldfootballR's pre-scraped `load_` files**
  (`JaseZiv/worldfootballR_data` GitHub releases), not live scraping. They're
  plain file downloads from GitHub, so FBref's bot protection doesn't apply.
  The `403` in #17 came from the *live* function
  `fb_big5_advanced_season_stats()`; the `load_` family worked first time.
- **The source is archived** (2025-09-18) and frozen, so some stat types stop
  before the end of the window. The verified coverage map, the gap list and the
  rules for using the data are in
  [`phase-1-data-coverage.md`](phase-1-data-coverage.md). That document replaces
  section 4 below: the Kaggle evaluation is no longer the first step, and the
  `load_` check in step 3 is done.
- **The browser route (soccerdata) is only used to fill specific gap pages**,
  one page per process, launched by Tyler. The first test page is
  `03_probe_one_page.py player gca 2324`.

### Probe result: `player gca 2324` — failed, same as before

Tyler ran `03_probe_one_page.py player gca 2324` in a fresh process, from `main`
after the probe fix, with Chrome closed. **It failed exactly as on 9/11:** the
first request took seleniumbase's Cloudflare "special" path, then died in the
reconnect step with `connection refused` on a fresh port. The traceback matched
the 9/11 pattern. (Reported by Tyler; the full output wasn't pasted into the
session.)

So the failure isn't tied to one session's state. It survives a reboot, fresh
processes, the McAfee removal, and having only one live request per process.

### Why it can't be dodged: the mechanism (#14, now confirmed by source)

Reading seleniumbase 4.54.0's source explains every failure:

1. **The "special" path is decided by a plain HTTP pre-check, not the
   browser.** `uc_special_open_if_cf` (`seleniumbase/core/browser_launcher.py`)
   sends a plain `requests` call to the URL before opening it, and takes the
   special path if that returns 3xx/4xx/5xx or a challenge page.
2. **FBref returns `403` to every plain HTTP client (#17).** So the special path
   runs on **every FBref page, every time**. It isn't luck, and separate
   processes don't avoid it. That rules out the idea of "use only the pages that
   don't trigger the challenge": there are none.
3. **The special path restarts chromedriver, and the restart hides its own
   failure.** `with driver:` calls `reconnect()`
   (`seleniumbase/undetected/__init__.py`), which stops chromedriver, starts it
   again, and opens a new session. On Windows it does a second stop/start for
   any `chrome-extension://` window. **Every step is wrapped in
   `suppress(Exception)`, and the function then sets `_is_connected = True`
   whether or not any step worked.** When the restart fails, the error is
   thrown away and the next command hits a port nothing is listening on:
   `connection refused`.

Why the restart fails on this machine specifically is still unknown, because
that error is suppressed. It doesn't need solving (below).

Why the 82.8s and 14.7s fetches on 9/11 got through remains unexplained. The
most likely reading is that the reconnect sometimes succeeds, but that's a
guess.

Upgrading isn't a real option: 4.54.1 is the only newer release, a single patch
version. Its code wasn't checked, and nothing here relies on it.

### The fix: seleniumbase Pure CDP mode, no chromedriver at all

seleniumbase 4.54.0 (already installed) includes **Pure CDP mode**
(`sb_cdp.Chrome`), which drives a real Chrome directly over the DevTools
protocol, with **no chromedriver process**. With no chromedriver there's no
reconnect, so the failing path can't run.

- `scripts/04_fetch_fbref_cdp.py` fetches named gap pages this way, saves each
  into soccerdata's cache under the filename soccerdata expects, then parses and
  checks it. Anything that isn't a complete, populated season is renamed
  `*.rejected.html` so it can't be used by mistake.
- `FBrefCached` in `scripts/fbref_extended.py` parses from the cache **without
  ever starting Chrome** (soccerdata's own readers launch a browser as soon as
  they're created, even for cached pages). Tested on the cached 2017/18 team
  page: 98 rows, 0.4s, no browser.
- `--dry-run` prints URLs and cache names without launching anything. It was
  checked against all 9 Tier 1 pages.

**Not yet proven:** nothing using CDP mode has been run against FBref. The
first run is one page (`gca:2324`), by Tyler.

**Deterministic fallback if CDP mode also fails:** Tyler opens each gap URL in
his normal Chrome, saves it (Ctrl+S, "Webpage, HTML Only") under the cache
filename that `--dry-run` prints, and `04_fetch_fbref_cdp.py --dry-run` then
verifies the saved files with the same checks. A person's own browser gets
through Cloudflare, so this works regardless of any automation bug: about
20–30 seconds per page, roughly 5 minutes for Tier 1's 9 pages.

### First Pure CDP run: `gca:2324` — the fetch works, but the data is blank

Tyler ran `04_fetch_fbref_cdp.py gca:2324`. **The fetch worked:** the page came
back in about 6 seconds, with no Cloudflare trouble, no reconnect and no
`connection refused`. Pure CDP mode fixes the scraping problem.

**The data on the page is empty.** The saved HTML (kept as
`players_Big 5 European Leagues Combined_2324_gca.rejected.html` in the
soccerdata cache) was inspected directly:

- The `stats_gca` table is there, with FBref's normal headers (`SCA`,
  `SCA Types`, `GCA`, `GCA Types`; cells `data-stat="sca"`, `"gca"` and so on).
  The checker was matching the right columns.
- It has all **2,852 player rows**, and `minutes_90s` and `age` are filled in
  every row. The top player has 38.0 90s, so it's a complete season, not a
  partial or blocked page.
- **Every SCA and GCA cell, including all the sub-type columns, is an empty
  string in all 2,852 rows.** FBref served the table with those columns blank.
- There's no notice on the page explaining it. It doesn't mention Opta, but
  neither does the 9/11 Big 5 page, so that proves nothing.

So the page was rejected correctly. The checker's wording was improved so
this case reads as "column exists but every cell is blank in FBref's HTML",
separate from a failed fetch.

### Live FBref no longer serves advanced data (`defense:2324`, `standard:2324`)

Tyler fetched both pages with `04` (both fetched fine; both rejected by the
check). The raw saved HTML was then inspected column by column, with the same
method as the GCA page. Three different stat pages, one pattern:

| Page | Advanced columns | What's still there |
|---|---|---|
| gca 2023/24 | All SCA/GCA columns present, **blank in all 2,852 rows** | Player, club, age, 90s |
| defense 2023/24 | Tkl, tackles by third, challenges, blocks, Tkl+Int, clearances, errors: **blank in all 2,852 rows** | Int and TklW 100% filled |
| standard 2023/24 | Expected (xG, npxG, xAG), Progression (PrgC, PrgP, PrgR) and xG-based per-90 columns: **removed entirely** | Minutes, goals, assists, cards and their per-90s |

What the checks ruled out:

- **Renamed columns (a checker bug).** No. The defense cells use FBref's usual
  `data-stat` names (`tackles`, `clearances`, `blocks`, `challenges`...). They
  just contain nothing.
- **"xG is on a different page by design."** No. worldfootballR scraped this
  exact player standard URL, and its snapshot has `xG_Expected`,
  `xAG_Expected` and `PrgP_Progression` 99.9% populated for 2023/24. The live
  file has no `xg`, `xa` or `progressive` attribute anywhere, including inside
  HTML comments. Its only other table is a hidden `compare_standard` widget;
  there's no Shooting or Passing sub-table on the page.
- **One page being odd.** Three stat types, same signature: every
  advanced-data column is blank or gone, while basic stats remain.

**The survivors aren't the old numbers.** Int and TklW, the two defensive
columns still filled (both also appear on FBref's basic `misc` page), were
compared player by player with the snapshot for 2023/24 (2,651 matched
player-club rows):

- Only about 61% of players have identical values (Int 61.6%, TklW 61.4%).
- They're close (correlation 0.996 and 0.997), but totals differ: Int is
  −2.1% (26,407 vs 26,981) and TklW +0.8% (32,587 vs 32,320), with players
  moving in both directions.

So FBref didn't just hide the advanced columns. The numbers it still shows
come from a different data version. Combined with the blanked and removed
columns, the most likely explanation is that FBref no longer has the Opta
advanced feed and now builds its stats from a different, basic source. **This
is an inference from the data; the pages don't announce it.**

**FBref also renamed clubs.** 198 of the 201 live rows that didn't match the
snapshot are the same FBref player IDs under new club names: "Nott'ham Forest"
is now "Nottingham", "Eint Frankfurt" is "Frankfurt", "Paris S-G" is
"Paris SG", "Sheffield Utd" is "Sheffield United", "Betis" is "Real Betis",
"Newcastle Utd" is "Newcastle". Player IDs are stable; club names aren't.

**Consequences:**

1. The browser route can't fill any advanced-stat gap. Tier 1 and Tier 2
   pages would come back blank or missing, and even the surviving basic
   columns would fail rule 4 (they don't match the snapshot).
2. The frozen worldfootballR snapshot (archived 2025-09-18) is effectively
   the last copy of FBref's Opta-era data. Protect it: it's step 1 of the plan
   (freeze it locally, with a manifest).
3. The club mapping table must join on FBref's stable team IDs (from team
   URLs), not club names.

## Summary

- **Scope question: settled.** 2017/18 is a valid start for the window (section 1).
- **Scraping FBref without a browser is blocked.** worldfootballR's plain-HTTP
  request for 2017/18 player standard stats got a flat `403 Forbidden` on both
  home Wi-Fi and a phone hotspot. It isn't tied to one IP address; FBref
  appears to reject any client that isn't a real browser.
- **The browser route is unresolved.** soccerdata/seleniumbase gets through on
  the first live request in a process, then a later request fails with
  `connection refused` on the local chromedriver port, on every retry. The
  leading explanation is seleniumbase's Cloudflare reconnect (#14). All the
  environmental causes we could think of have been ruled out.
- **New route to evaluate first:** a pre-built Kaggle dataset, "FBref 2017-2024
  for Europe's Top 5 leagues" (section 4). If it's usable, it could cover most of
  the window, leaving only the most recent season or two to scrape.

## 1. Scope question (resolved)

- The "zero xG" result was real but limited to one table: the Big 5 combined,
  team-level `standard` table (`stats_teams_standard_for`). The raw cached HTML
  has 0 xG `data-stat` attributes out of 4,698, and 29 distinct columns. The
  parser didn't drop anything.
  - **Correction (added after the Pure CDP runs):** at the time this was
    explained as "that table has no Expected columns in any season; xG lives on
    the shooting/passing pages". **That was wrong.** worldfootballR scraped
    the same team standard page, and its snapshot has `xG_Expected` and
    `PrgP_Progression` 100% populated for 2017/18. The page *used to* carry
    them. The missing xG was the first visible sign that FBref had removed its
    advanced data (see "Live FBref no longer serves advanced data" below), not
    a feature of how the page is built.
- FBref's own 2017-18 La Liga and Bundesliga pages say advanced data is
  provided by Opta for those competitions. A public notebook pulls the same
  Big 5, 2017-18-onward dataset successfully.
- Follow-ups:
  - Scoping doc section 4.1 says the data is from StatsBomb. FBref now
    credits Opta. Update the wording.
  - Whatever the source ends up being, confirm xG is actually present and
    populated in the player-level shooting/passing data. That's a data check;
    the scope question is already answered.

## 2. What happened (all 2026-09-11)

| Time | Launched by | Tool / script | Outcome |
|---|---|---|---|
| ~12:02 | Claude (sandboxed) | 01 | Killed, exit 137. Chrome was stuck on its profile picker. |
| 12:09–12:19 | Claude (sandboxed) | 01 | `read_leagues` came from cache. `read_seasons` failed with connection refused, then invalid session id, then the CAPTCHA path. seleniumbase pip-installed `pyautogui` and 7 other packages mid-run. Stopped. |
| 12:15 | Tyler | 01 | CAPTCHA on `read_seasons`, then `PermissionError` on seleniumbase's pip lock, because it collided with the run above. |
| 12:18 | Tyler | 01 | **`read_seasons` succeeded in 82.8s.** Live fetch, got through Cloudflare. |
| 12:27 | Tyler | 02 | **Team `standard` succeeded in 14.7s.** Team `possession` failed attempts 1–4 with connection refused, and the driver port changed each time (60463 → 59362 → 56572). Stopped with Ctrl-C. |
| after reboot | Tyler | soccerdata | Reported as the same failure. Output wasn't pasted into the session; paste it next time if you kept it. |
| evening | Tyler | McAfee, Windows Firewall | Both checked, neither involved. |
| evening | Tyler | worldfootballR (R, plain HTTP) | **`403 Forbidden`** on 2017/18 player standard stats. Same result on home Wi-Fi and on a phone hotspot. |

Pattern: through a real browser, the first live request per process succeeded
both times it was captured, and the failure came on a later one. Without a
browser, requests are refused before any data comes back, whatever the network.

## 3. Theories and status

| # | Theory | Status | Evidence |
|---|---|---|---|
| 1 | Chrome profile picker blocking launch | Fixed | Caused the first exit 137. Fixed by selecting a profile. |
| 2 | Claude's sandbox kills Chrome | Confirmed, worked around | Every Claude-launched run died. Rule now: Tyler launches every scraper run. |
| 3 | Cloudflare blocks FBref outright | Ruled out (for browsers) | Two live browser fetches got through (82.8s, 14.7s). Non-browser clients are a different story, see #17. |
| 4 | Parser dropping xG columns | Ruled out | The raw HTML has no xG to drop (section 1). |
| 5 | Script creating a new reader per stat type | Ruled out | `02` creates one reader (line 84) and reuses it. |
| 6 | Orphaned `uc_driver.exe` processes | Ruled out | The reboot cleared them and the failure reportedly continued. |
| 7 | Two runs fighting over the same driver/lock | Partly | Explains the 12:15 `PermissionError`. Doesn't explain 12:27, because Claude's run had been stopped by then. |
| 8 | Windows Defender | Ruled out | Protection History showed nothing relevant (Tyler checked). |
| 9 | Chrome/driver version mismatch | Ruled out | Chrome `153.0.8010.36` is the only installed version (exe dated 9/7). `uc_driver.exe` reports ChromeDriver `153.0.8010.36`; it lives in `.venv\Lib\site-packages\seleniumbase\drivers\` and was downloaded 9/11 12:02. A mismatch fails at session creation with `session not created`, which never appeared. |
| 10 | System proxy | Ruled out | WinHTTP is set to direct access, WinINET `ProxyEnable=0`, no proxy environment variables. |
| 11 | DNS filter | Unlikely | Wi-Fi uses Comcast DNS (75.75.75.75 / 75.75.76.76). A DNS block would stop the first request too. |
| 12 | VPN | Not re-checked, low priority | ExpressVPN 12.49 is installed; its adapters were Disconnected at end of session. #16 shows the refusals aren't tied to one IP, so this matters less than it did. Still worth confirming it's off before the next browser run. |
| 13 | McAfee (WebAdvisor + Framework Host running) | Ruled out, removed | Tyler found it the evening of 9/11. The subscription had expired 3+ years earlier, so it was uninstalled. The failure continued afterwards. |
| 14 | seleniumbase reconnect path | **Confirmed by source code (see Resolution). Bypassed with Pure CDP mode** | `browser_launcher.py:516` has a bare `with driver:`. Its `__exit__` calls `reconnect()` (`seleniumbase/undetected/__init__.py`), which stops chromedriver and restarts it inside `suppress(Exception)`, so any restart error is thrown away and the next command gets connection refused. This only fires when a response looks like Cloudflare (3xx/4xx/5xx or a challenge page), and soccerdata's 7s spacing makes that likely by the second request. |
| 15 | Windows Firewall | Ruled out | Tyler checked the evening of 9/11. |
| 16 | IP-level block | Ruled out | The same plain-HTTP `403` on home Wi-Fi and on a phone hotspot, which are different IPs and different carriers. Browser requests from the home IP got through (#3). |
| 17 | Scraping FBref without a browser | **Blocked** | worldfootballR's plain-HTTP request got `403` on both networks. FBref appears to require a real browser. This rules out live scraping without a browser; it says nothing about pre-scraped copies hosted elsewhere (section 4). |

## 4. Next session, in order

The first three steps need no browser at all.

1. **Paste the post-reboot soccerdata output** if you kept it.

2. **Evaluate the Kaggle dataset "FBref 2017-2024 for Europe's Top 5 leagues."**
   Tyler downloads it (it goes under `data/`, which is gitignored). Check:
   - **License.** Read the dataset's license on Kaggle. The underlying data
     belongs to Sports Reference/Opta, so an uploader's license can't grant more
     than they had. Safe approach for a public portfolio: use it for analysis,
     credit FBref/Opta and the dataset, and never commit the raw files (already
     true, `data/` is gitignored).
   - **Exact seasons.** Does "2017-2024" end at 2023/24 or 2024/25? That decides
     whether one or two seasons (2024/25, 2025/26) are left to get elsewhere.
   - **Leagues and grain.** All five leagues, at **player level** (the scoring is
     per player, per section 4.4 of the scoping doc), not just team totals.
   - **Stat types.** Standard, shooting, passing, defense, possession and GCA,
     with the columns the scoring needs actually populated in 2017/18, not just
     present: xG/npxG, progressive passes and carries, tackles, interceptions,
     SCA.
   - **Same data provider as a fresh scrape.** FBref renamed columns when it
     moved to Opta (for example `xA` became `xAG`, and `Prog` split into
     `PrgP`/`PrgC`/`PrgR`). Old-style names would suggest the snapshot predates
     the switch, so its early seasons could come from a different xG model than
     anything we scrape now. Mixing the two would break the "one consistent data
     standard" claim in scoping doc 4.1. Check this before combining sources.
   - **Player ID.** Is there an FBref player ID or URL column?
     `player_dictionary_mapping()` (Phase 1 item 5) joins FBref to Transfermarkt
     on FBref URLs. Without them the join falls back to player names, which
     brings back the entity-matching risk the mapping exists to avoid.
   - **Team names.** Record how clubs are spelled; this feeds the club mapping
     table (Phase 1 item 6).
   - **Completeness.** Player-season row counts by season and league, to spot
     missing chunks.
   - If it's usable, add a `source` column per season in the warehouse, so any
     join between Kaggle and scraped data stays visible and can be explained in
     the README.

3. **Check worldfootballR's `load_fb_big5_advanced_season_stats()`.** It
   downloads pre-scraped files from GitHub rather than requesting FBref, so the
   `403` in #17 shouldn't apply. (If this was the function that returned the
   `403`, note it here; that changes things.) Run the same checklist as step 2,
   paying particular attention to how recent its seasons go. It may fill
   whatever gap the Kaggle data leaves.

4. **Decide what's still missing** after steps 2–3. Probably one or two recent
   seasons: about 6 stat pages each, around 12 pages in total.

5. **For those pages only, run the `03` probe.** Confirm ExpressVPN is off first.
   It waits 25s between requests and fetches one page per process:
   `cd /d C:\Users\tyler\Documents\GitHub\Transfer-Market-Efficiency && .venv\Scripts\python scripts\03_probe_one_page.py player standard`
   Since the first request in each process has got through both times we
   captured it, a fresh process per page may be enough for about a dozen pages,
   even if the reconnect bug stays unfixed.
   - If it succeeds, loop it: one process per page, cached, resumable.
   - If it fails the same way, go to step 6.

6. **Claude writes a small wrapper** that logs the exception
   `suppress(Exception)` is swallowing inside `reconnect()`, and Tyler runs it.
   That shows the actual reason chromedriver won't restart, instead of us
   guessing.

## 5. Loose ends

- `requirements.txt` is missing the 8 packages seleniumbase installed mid-run
  (`pyautogui` 0.9.54 and its dependencies). Regenerate it once the data route
  is settled.
- Scoping doc section 4.1: change StatsBomb to Opta, and describe the data
  source(s) actually used once step 2 is decided.
- Phase 1 items 4–8 (Transfermarkt, player mapping, club mapping, metadata,
  trophies) haven't started. None of them depend on FBref. Transfermarkt is a
  different site, so the `403` above doesn't apply, but it hasn't been tested
  and may have its own bot protection.
