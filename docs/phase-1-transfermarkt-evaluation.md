# Phase 1 — Transfermarkt source evaluation: `transfermarkt-datasets`

Evaluated 2026-09-11 against the four questions that decide whether it can be
the Transfermarkt source: club coverage, undisclosed fees, loans, and manager
data. Everything below comes from the downloaded tables, the project's own
transformation code, and one Transfermarkt page fetched for comparison.

## The source

- GitHub `dcaribou/transfermarkt-datasets`, CC0 licence, built from
  Transfermarkt. Published as CSVs on a public R2 bucket, a DuckDB file and a
  Kaggle dataset (`davidcariboo/player-scores`).
- **Frozen since July 2026.** The maintainer's status announcement (GitHub
  discussion #383, 2026-09-05) says collection has failed since mid-July
  because Transfermarkt pages are no longer reliably reachable from GitHub's
  cloud runners, and updates are "paused indefinitely". `games` stops at
  2026-07-06, `appearances` at 2026-06-28, `player_valuations` at 2026-06-12,
  and **the summer 2026 transfer window is incomplete**. The scored window
  (2017/18–2023/24) is unaffected; the 2026/27 recency layer (scoping doc 4.8)
  is.

## Answers

| Question | Answer |
|---|---|
| **All 145 clubs, seven seasons?** | **Yes, complete.** Every one of the 35 league-seasons has the same number of clubs as the FBref snapshot (20/20/20/18/20; Ligue 1 18 in 2023/24), 145 distinct clubs overall, and every league match is present (Ligue 1 2019/20 has 279, the COVID-curtailed season). |
| **Undisclosed fees?** | **NULL.** Transfermarkt's `?`, `-` and blank all become NULL, so undisclosed and "no information" (`-`) can't be told apart. 11.0% of transfers touching the 145 clubs in the window are NULL. |
| **Loans vs permanent?** | **Not distinguished.** There's no transfer-type column. `free transfer`, `loan transfer` and `End of loan` all become **0**, so a loan and a free transfer look identical. Loan returns are separate rows with fee 0, dated 30 June and filed in the season that's ending. 65.6% of rows in the window have fee 0. |
| **Are loan fees populated?** | **No, they're discarded.** Transfermarkt writes them as `Loan fee:€5.80m`; the parser only reads strings starting with `€`, and everything else becomes 0. |
| **Manager / staff data?** | **No staff table, no appointment or departure dates, no manager IDs.** What it has is the **manager's name for every match** (`games.home/away_club_manager_name`, `club_games.own_manager_name`): filled for 100% of the 12,607 league games in the window, and for cup and European games too. |

### Evidence for the fee and loan answers

The mapping comes straight from the project's dbt model
(`dbt/models/base/transfermarkt_api/base_transfers.sql`):
`'-', '?', ''` → NULL; `'free transfer'` → 0; strings starting with `€` → the
number; **anything else → 0**.

Checked in the data:

- Coutinho's 2019 loan to Bayern (reported loan fee about €8.5m) is stored as
  `0`, the same as Messi's 2021 free transfer to PSG. His 2020 return to
  Barcelona is its own row, also `0`.
- **Chelsea 2023/24, compared with Transfermarkt's own page** (fetched once,
  plain HTTP): the page lists 16 loans out, 6 of them with a loan fee. The
  dataset stores all six as 0: Lukaku €5.8m, Broja €4.7m, Moreira €2.8m,
  Maatsen €2.3m, Kepa €1.0m, Hutchinson €1.0m, so €17.6m in loan fees
  received is missing for one club-season.
- Otherwise the dataset and the page agree: all 75 page rows are accounted for
  (7 loan returns dated 30/06/2023 are filed under 2022/23). The dataset only
  includes transfers of players in its `players` table; this one club-season
  showed no losses from that, but it's a spot check, not a proof.

### Evidence for the manager answer

Per-match names reproduce known histories exactly: Chelsea 2022/23 is Tuchel
(6 league games), Potter (22), caretaker Bruno Saltor (1), Lampard (9); Bayern
2022/23 is Nagelsmann (25) then Tuchel (9); Real Madrid 2023/24 is Ancelotti
for all 38. Of 684 club-seasons in the window, 431 had one manager, 150 had
two, and 103 had three or more (caretakers included).

## What this means

**Usable as is:**
- Clubs and fixtures, including **league points** for the sporting-return
  pillar (scoping doc 5), from match results.
- Transfer fees for permanent moves, with undisclosed fees as NULL, which fits
  scoping doc 7 ("excluded and documented, not guessed").
- Market value at the time of each transfer (`transfers.market_value_in_eur`)
  and market value history (`player_valuations`), for the trading-profit and
  squad-value pillars.
- **Manager tenures for scoping doc 4.9.** 4.9 snaps tenure bands to season
  boundaries, and the per-match names support that directly: who managed which
  matches, caretakers included. What they *don't* give is official appointment
  and departure dates, or a stable manager ID; names would have to be kept
  consistent by hand if the same person is spelled two ways.

**Not usable without a supplement: loans.**
- The dataset can't separate loans from free transfers, drops loan fees, and
  mixes loan returns in with real moves. The club page's permanent / loan /
  free spending breakdown (scoping doc appendix) can't be built from it, and
  loan fees received (€17.6m for Chelsea alone in 2023/24) would be missing
  from the money flows.
- Transfermarkt's own club transfer pages have exactly what's missing: every
  row is labelled (`Loan fee:€X`, `loan transfer`, `End of loan`,
  `free transfer`, `?`), and they answered a plain request from the home
  connection. Covering the window is 145 clubs × 7 seasons, about 1,015 pages,
  or about 1,450 including the three recency seasons.

## Decisions (Tyler, 2026-09-11)

1. **Adopted.** `transfermarkt-datasets` is the Transfermarkt source for clubs,
   fixtures and league points, transfer fees, market values and managers.
2. **Loan limitation accepted; no live fetches.** ~~Loan fees aren't recovered,
   and loans aren't separated from free transfers.~~
   **Superseded 2026-09-12.** A separate finding — the frozen table is missing
   real fee-bearing signings, unevenly across the window — forced a full pull of
   Transfermarkt's club transfer pages anyway, and those pages label every row.
   Loan fees are recovered (€2,189m across 1,617 loans) and the club page's
   spending breakdown is three-way again. See
   [`phase-1-data-coverage.md`](phase-1-data-coverage.md) and
   [`phase-2-schema.md`](phase-2-schema.md) section 2.
3. **Managers from the per-match names.** Tenure bands for scoping doc 4.9 are
   built from them. There are no official appointment dates, and names are
   checked by hand for spelling variants because there are no manager IDs.
