# Phase 1 — FBref scraper diagnostics

Status at end of session, 2026-09-11. Written so the next session starts here
instead of re-deriving it.

## Summary

- **Scope question: settled.** 2017/18 is a valid start for the window (section 1).
- **Scraper: unresolved.** In Tyler's terminal, the first live request in a
  process has got through both times we captured it. A *later* request in the
  same process then fails with `connection refused` on the local chromedriver
  port, on every retry, until killed.
- **Best remaining leads, neither confirmed:** McAfee interfering with the
  chromedriver relaunch (#13), and seleniumbase's reconnect path (#14). They
  fit together: #14 explains *when* chromedriver gets relaunched, #13 could
  explain *why the relaunch fails*.

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
  - Once scraping works, confirm xG actually parses from the player-level
    shooting/passing pages through `fbref_extended.py`. That's a parsing
    check; the scope question is already answered.

## 2. What happened (all 2026-09-11)

| Time | Launched by | Script | Outcome |
|---|---|---|---|
| ~12:02 | Claude (sandboxed) | 01 | Killed, exit 137. Chrome was stuck on its profile picker. |
| 12:09–12:19 | Claude (sandboxed) | 01 | `read_leagues` came from cache. `read_seasons` failed with connection refused, then invalid session id, then the CAPTCHA path. seleniumbase pip-installed `pyautogui` and 7 other packages mid-run. Stopped. |
| 12:15 | Tyler | 01 | CAPTCHA on `read_seasons`, then `PermissionError` on seleniumbase's pip lock, because it collided with the run above. |
| 12:18 | Tyler | 01 | **`read_seasons` succeeded in 82.8s.** Live fetch, got through Cloudflare. |
| 12:27 | Tyler | 02 | **Team `standard` succeeded in 14.7s.** Team `possession` failed attempts 1–4 with connection refused, and the driver port changed each time (60463 → 59362 → 56572). Stopped with Ctrl-C. |
| after reboot | Tyler | ? | Reported as the same failure. Output wasn't pasted into the session; paste it next time if you kept it. |

Pattern: the first live request per process succeeded both times it was
captured, and the failure came on a later one. That matters because a
network-level block (proxy, DNS filter) would normally stop the *first*
request as well.

## 3. Theories and status

| # | Theory | Status | Evidence |
|---|---|---|---|
| 1 | Chrome profile picker blocking launch | Fixed | Caused the first exit 137. Fixed by selecting a profile. |
| 2 | Claude's sandbox kills Chrome | Confirmed, worked around | Every Claude-launched run died. Rule now: Tyler launches every scraper run. |
| 3 | Cloudflare blocks FBref outright | Ruled out | Two live fetches got through (82.8s, 14.7s). |
| 4 | Parser dropping xG columns | Ruled out | The raw HTML has no xG to drop (section 1). |
| 5 | Script creating a new reader per stat type | Ruled out | `02` creates one reader (line 84) and reuses it. |
| 6 | Orphaned `uc_driver.exe` processes | Ruled out | The reboot cleared them and the failure reportedly continued. None were running at end of session. |
| 7 | Two runs fighting over the same driver/lock | Partly | Explains the 12:15 `PermissionError`. Doesn't explain 12:27, because Claude's run had been stopped by then. |
| 8 | Windows Defender | Ruled out | Protection History showed nothing relevant (Tyler checked). |
| 9 | Chrome/driver version mismatch | Ruled out | Chrome `153.0.8010.36` is the only installed version (exe dated 9/7). `uc_driver.exe` reports ChromeDriver `153.0.8010.36`; it lives in `.venv\Lib\site-packages\seleniumbase\drivers\` and was downloaded 9/11 12:02. A mismatch fails at session creation with `session not created`, which never appeared. |
| 10 | System proxy | Ruled out | WinHTTP is set to direct access, WinINET `ProxyEnable=0`, no proxy environment variables. |
| 11 | DNS filter | Unlikely | Wi-Fi uses Comcast DNS (75.75.75.75 / 75.75.76.76). A DNS block would stop the first request too. |
| 12 | VPN | Open, cheap to rule out | ExpressVPN 12.49 is installed. Both its adapters were Disconnected at end of session, but we don't know whether it was connected during the runs. VPN exit IPs get far more Cloudflare challenges. |
| 13 | Third-party security product | **Open, best new lead** | McAfee is installed, and the **McAfee WebAdvisor** and **McAfee Framework Host** services are running. The Defender check doesn't cover McAfee. seleniumbase runs a *patched* chromedriver binary and relaunches it on reconnect, which is the kind of behaviour AV heuristics act on. |
| 14 | seleniumbase reconnect path | Open, leading code-side hypothesis | `browser_launcher.py:516` has a bare `with driver:`. Its `__exit__` calls `reconnect()` (`seleniumbase/undetected/__init__.py`), which stops chromedriver and restarts it inside `suppress(Exception)`, so any restart error is thrown away and the next command gets connection refused. This only fires when a response looks like Cloudflare (3xx/4xx/5xx or a challenge page), and soccerdata's 7s spacing makes that likely by the second request. |

## 4. Next session, in order

1. Paste the post-reboot output if you kept it.
2. Checks that don't launch a browser:
   - Confirm ExpressVPN is disconnected.
   - Open McAfee and check Security history and Quarantine for anything on
     9/11 between about 12:00 and 12:35, or anything mentioning
     `uc_driver.exe`, `chromedriver` or `python.exe`.
3. Run the `03` probe once. It waits 25s between requests and fetches one
   page per process:
   `cd /d C:\Users\tyler\Documents\GitHub\Transfer-Market-Efficiency && .venv\Scripts\python scripts\03_probe_one_page.py player standard`
   - If it succeeds, the rate-limit/reconnect explanation fits. Build the full
     pull around slow spacing and a resumable cache.
   - If it fails the same way, go to step 4.
4. Claude writes a small wrapper that logs the exception `suppress(Exception)`
   is swallowing inside `reconnect()`, and Tyler runs it. That shows the
   actual reason chromedriver won't restart, instead of us guessing.
5. If McAfee turns out to be involved, add an exclusion for the venv's
   `seleniumbase\drivers` folder in McAfee (Tyler does this) rather than
   turning protection off.

**Fallback if FBref scraping can't be made reliable:** worldfootballR (already
installed) has `load_fb_big5_advanced_season_stats()`, which loads pre-scraped
Big 5 FBref data from GitHub with no browser involved. Not yet checked: whether
it covers 2025/26 and every stat type we need. It's a 5-minute check and worth
doing before spending more time on Selenium.

## 5. Loose ends

- Uncommitted: `scripts/fbref_extended.py` (the team-level override),
  `scripts/02_verify_advanced_stats_1718.py`, `scripts/03_probe_one_page.py`,
  and this file.
- `requirements.txt` is missing the 8 packages seleniumbase installed mid-run
  (`pyautogui` 0.9.54 and its dependencies). Regenerate it once scraping works.
- Scoping doc section 4.1: change StatsBomb to Opta.
- Phase 1 items 4–8 (Transfermarkt, player mapping, club mapping, metadata,
  trophies) haven't started. None of them need the FBref scraper;
  worldfootballR's Transfermarkt functions use plain HTTP, not Chrome, so that
  work can go ahead in parallel.
