# Phase 3 — the scoring layer

R reads the warehouse, computes each part of the efficiency score, and writes the
results to the `score` schema. Every R result is recomputed independently in
SQL, and a run fails unless the two agree.

| Script | Writes | Verified by |
|---|---|---|
| `R/10_player_quality.R` | `score.player_quality`, `score.player_quality_metric` | `sql/11_check_player_quality.sql` |
| `R/20_sporting_return.R` | `score.club_season_sporting` (Pillar 4) | `sql/21_check_sporting_return.sql` |

Run from the repository root, after any warehouse load (the score tables hold
the warehouse's surrogate keys, which a reload renumbers):

```
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\10_player_quality.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\20_sporting_return.R
```

**R setup.** R 4.4.1 with DBI, RPostgres, dplyr and tidyr in the user library.
CRAN no longer builds Windows binaries for R 4.4, and the compiled packages
won't build from source without Rtools. So the packages came from Posit Package
Manager, which serves the same CRAN packages prebuilt. Credentials come from
`.env` through libpq, the same as the Python loader.

## 1. Player quality (scoping doc 4.4, 4.5)

One row per player per club per season: how good was this player's output per
90 minutes, compared with others in the same job in the same season.

### Method

1. **Floor.** At least 900 minutes for that club that season. Below that, a
   per-90 rate is noise. The row is kept, unscored, with the reason recorded.
2. **Metrics per group** (the `METRICS` table in the script):

   | Group | Metrics |
   |---|---|
   | GK | PSxG minus goals against per 90, save % |
   | CB | tackles won, interceptions, clearances, aerials won (all per 90), aerial win %, progressive passes per 90 |
   | FB | tackles won, interceptions, progressive passes, progressive carries, SCA (per 90) |
   | CM | tackles won, interceptions, progressive passes, passes into final third, progressive carries, SCA (per 90) |
   | AM/W | SCA, xAG, npxG, take-ons won, progressive carries (per 90) |
   | FW | npxG, goals, xAG, SCA (per 90) |

3. Each metric is z-scored within **season x position group**, over scored
   rows only. Standardising within the season also absorbs the pre-2022/23
   passing-file vintage.
4. The player's composite is the mean of those z-scores. It is then
   re-standardised within season x group, so `quality_z` = 0 means the group
   average and 1 means one standard deviation, for every group.
   `quality_pctile` is its percent rank within the group, for display.

### Missing inputs are gaps, never zeros

A row over the floor that is missing one metric is scored on the rest, and
`n_metrics` shows it. A row missing more, or left with fewer than two, is
unscored, with the reason "advanced stats missing from the FBref snapshot".

- **Karazor's three Stuttgart seasons** have no SCA (the documented gap). They
  are scored on 5 of 6 metrics.
- **9 of the 13 blank-filled player-seasons** have only minutes, goals and
  Kaggle SCA. They are unscored.

The two unscored reasons stay separate on purpose. Later pillars must never
treat a data gap as if the player had not played enough.

### Centre-backs: defensive volume adjusted for team possession

**The problem.** A centre-back's tackles, interceptions, clearances and aerial
duels depend on how much their team defends. Scored raw, the rankings failed
the scoping doc's football-judgement test (section 5, "Validation"):
volume defenders at weaker teams topped the group, while elite centre-backs
at dominant teams ranked low.

Three treatments were tested on the same data (average percentile across the
player's seasons):

| Player | Raw | Full adjustment (StatsBomb-style) | **Residual (adopted)** |
|---|---|---|---|
| Rúben Dias | 25 | 81 | **45** |
| Milan Škriniar | 10 | 48 | **18** |
| John Stones | 39 | 82 | **58** |
| Aymeric Laporte | 53 | 85 | **67** |
| Virgil van Dijk | 88 | 97 | **93** |
| Alexander Djiku (Strasbourg) | 93 | 71 | **91** |
| Correlation of CB percentile with team possession | 0.02 | 0.75 | **0.23** |

**Decision (Tyler, 2026-09-14): residual.** The full adjustment scales
defensive actions up for teams that have the ball. It fixed the famous names
but made centre-back quality track team possession at r = 0.75. That swaps one
club-size effect for another, and it would feed straight into the recruitment
ROI: big clubs' centre-back signings would score well by construction. It
conflicts with the project's central guardrail against the index becoming a
proxy for club size.

The residual removes only what possession explains. Within each season, each
of the four stats is regressed on the club's `possession_pct`, and the
residual is z-scored. It is the same idea as the scale normalisation planned
for the composite: a residual, not a ratio.

- `score.player_quality_metric` keeps the raw `value` beside the adjusted
  `value_used`, plus a `possession_adjusted` flag, so the adjustment can always
  be seen and undone.
- The SQL check recomputes the residuals with `regr_slope`/`regr_intercept`
  and confirms each adjusted stat has zero correlation with possession in
  every season.
- Only those four centre-back stats are adjusted. The same stats for CM and FB
  showed no link to possession (r −0.10 to 0.00), so they stay raw. Aerial win
  % (r 0.11) and progressive passes are rates or attacking output, and also
  stay raw.

Team possession comes from FBref's team possession table in the frozen
snapshot, which is already checksummed. It is loaded into
`fact_club_season.possession_pct`, and `03_checks.sql` confirms it for all 684
club-seasons.

### Known limitation: centre-backs are the worst-measured group

After the adjustment, the 2023/24 top ten reads credibly (Kim Min-jae,
Konaté, Hummels, van Dijk). Elite centre-backs at dominant clubs still sit
mid-table rather than near the top (Dias 45, Saliba 43, Rüdiger 42), and David
Alaba is in the 2022/23 bottom ten. Public event data records what a defender
*does*, not what they *prevent* by positioning. Stated plainly: **CB quality
scores are noisier than any other group's, and understate centre-backs at
possession-dominant clubs.**

*Revisit criterion:* if centre-back signings turn out to drive a club's
recruitment ROI ranking in a way that looks wrong on the eye test (section 5),
weight CB quality down in the club aggregate, or show it with an explicit
low-confidence marker, rather than adjusting the metric further to fit
reputations.

### Position groups: other remaining imperfections

- **Override cells still average slightly below zero.** Examples: a centre-back
  listed MF, scored as CM, averages −0.50; a forward or full-back listed as
  midfield, scored as AM/W, averages −0.36. These players often play out of
  position, so part of the gap may be real.
  *Revisit criterion:* if a specific cell shows a profile that clearly
  matches another group, as the wing-back cells did, move it the same way,
  with evidence.
- **Transfermarkt gives a player's current position, not a history.** Jules
  Koundé's Sevilla centre-back seasons are scored as a full-back, and Matheus
  Nunes's Wolves midfield season as a full-back. Only broad disagreements with
  FBref are caught.

### Results (load of 2026-09-14)

| | Player-seasons |
|---|---|
| All rows | 19,562 |
| Scored | 11,265 (89.3% of minutes) |
| Unscored: under 900 minutes | 8,288 |
| Unscored: advanced stats missing | 9 |

`sql/11_check_player_quality.sql` passes 20 of 20. R and SQL agree to 1e-9 on
every per-90 value, possession residual, metric z-score and `quality_z`.

The forward, attacker, full-back and central-midfield rankings pass the eye
test. These are the full ranges across each player's scored seasons, not a
best case, and most seasons fall in the 90s:

| Group | Player | Percentile range |
|---|---|---|
| FW | Lewandowski | 76–100 (the 76 is 2023/24, his age-35 season) |
| FW | Kane | 63–98 |
| AM/W | Salah | 81–98 |
| AM/W | Vinícius | 92–100 |
| FB | Alexander-Arnold | 79–100 |
| FB | Robertson | 70–99 |
| FB | Hakimi | 71–99 |
| CM | Rodri | 52–100 |
| CM | Kanté | 61–99 |
| CM | Brozović | 71–99 |

## 2. Pillar 4: sporting return (scoping doc 5)

One row per club-season: league points per match, standardised within
league-season.

- **Per match, not total points.** Bundesliga seasons and Ligue 1's 18-club
  2023/24 season have 34 matches, the rest 38, and Ligue 1 2019/20 was abandoned
  after 27–28. Totals would not compare.
- **Results points, not official points.** Deductions are administrative
  (Juventus 2022/23: 72 results points, listed 7th; Everton 2023/24).
  Sporting return measures what happened on the pitch. `has_known_deduction`
  carries the flag through, so the dashboard can footnote it.
- **`ppm_z` is within competition x season.** 0 is the league's average club
  that season. It compares a club with its own league, not across leagues.
  The raw `points_per_match` is kept beside it for the composite stage.

Checked data: 35 league-seasons and 684 club-seasons. Every league-season has
complete fixtures except Ligue 1 2019/20.

`sql/21_check_sporting_return.sql` passes 9 of 9. Points per match and `ppm_z`
match SQL to 1e-9, every league-season has mean 0 and sd 1, Ligue 1 2019/20 is
scored on its own 27–28 matches, and Juventus 2022/23 keeps its 72 results
points.
