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

### Probe result: `player gca 2324`

*Pending — Tyler runs it next. Record the outcome here either way: whether the
page came through, whether any retry or reconnect lines appeared, and whether
SCA/GCA is populated for a complete 38-matchweek season.*

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
  parser didn't drop anything; that table has no Expected columns in any
  season. xG lives on the shooting/passing pages.
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
| 14 | seleniumbase reconnect path | **Open, leading hypothesis** | `browser_launcher.py:516` has a bare `with driver:`. Its `__exit__` calls `reconnect()` (`seleniumbase/undetected/__init__.py`), which stops chromedriver and restarts it inside `suppress(Exception)`, so any restart error is thrown away and the next command gets connection refused. This only fires when a response looks like Cloudflare (3xx/4xx/5xx or a challenge page), and soccerdata's 7s spacing makes that likely by the second request. |
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
