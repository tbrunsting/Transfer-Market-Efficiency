# Phase 3 — the scoring layer

R reads the warehouse, computes each part of the efficiency score, and writes the
results to the `score` schema. Every R result is recomputed independently in
SQL, and a run fails unless the two agree.

| Script | Writes | Verified by |
|---|---|---|
| `R/10_player_quality.R` | `score.player_quality`, `score.player_quality_metric` | `sql/11_check_player_quality.sql` |
| `R/20_sporting_return.R` | `score.club_season_sporting` (Pillar 4) | `sql/21_check_sporting_return.sql` |
| `R/30_recruitment_roi.R` | `score.club_season_recruitment`, `score.signing_credit`, `score.league_premium`, `score.run_parameter` (Pillar 1) | `sql/31_check_recruitment_roi.sql` |

Run from the repository root, after any warehouse load (the score tables hold
the warehouse's surrogate keys, which a reload renumbers):

```
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\10_player_quality.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\20_sporting_return.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\30_recruitment_roi.R
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

## 3. Pillar 1: recruitment ROI (scoping doc 5)

One row per club-season: the output that season's signings delivered, per
deflated euro of fee. It is attributed to the signing window, so a trend line
reads "how well did this club recruit that summer and winter".

### How output is credited

Grounding showed that crediting output to a signing's first spell loses real
value. City paid €21.4m for Julián Álvarez and loaned him straight back to River
Plate. The loan ended the spell, and his City seasons were then credited to a
loan return, which is not a signing. 267 fee signings were affected. The rule
(`score.signing_credit`, one row per credited player-season):

- **Fee, free and undisclosed signings** keep the credit through loans out,
  until a permanent departure (sold, released, or an undisclosed move).
- **A loan's** credit ends at the next departure of any kind.
- **A newer arrival** to the same club always takes over, so no season is
  credited twice. Events on the same date do not end each other.
- **Output per season** = season-equivalents x quality weight.
  Season-equivalents = minutes / 90 / the club's league matches, so 34- and
  38-match seasons compare. Quality weight = `quality_pctile` / 100: 0 for a
  season under the 900-minute floor, and 0.5 (the group median) for the 9
  player-seasons whose stats are missing from the snapshot.
- **Horizon: three seasons**, the signing season plus two. For complete fee
  cohorts, 29% of a signing's output comes in the signing season, 26% the next
  and 19% the third, so two seasons would capture about 55% and three about
  74%.

**Spend** is disclosed fees that count as spend (permanent and loan fees), each
divided by that season's median permanent fee (scoping doc 4.6).
Per-season permanent fees reconcile exactly to `dim_season.total_fees_eur`.

### Guardrails

1. **Spend floor.** Club-seasons below the 10th percentile of spend (1.154
   median fees, about €5.7m) are labelled *insufficient spend* and not scored:
   69 of 684. Athletic Bilbao, with its Basque-only policy, is the most frequent.
2. **Log transform.** log ROI = log(output + 0.1) − log(spend). The offset keeps
   a cohort with no output finite; 0.5 instead moves club ranks by only
   rho 0.98.
3. **Scale normalisation.** log ROI is regressed on log squad value at season
   start, with season and league effects, and the residual is the score
   (`roi_normalised`, and `roi_z` = residual / sd). Squad value stands in for
   revenue, and the season effects are the 4.6 deflator in log form. Before
   normalising, log ROI and log squad value correlate at −0.68: bigger clubs
   pay far more per unit of output.

### Decisions (Tyler, 2026-09-14)

**1. League effects in the normalisation, with the league effect published as
a finding.** With season effects only, 11 of the bottom 12 clubs were Premier
League, with a mean `roi_z` of −0.82. Squad value does not capture Premier League
broadcast money, so the index was partly measuring "is this club English".
With league effects, each club is judged against its own league, and the
bottom twenty spreads across all five leagues (Bundesliga 6, La Liga 6, Ligue 1
4, Serie A 2, Premier League 2).

The league effect is not thrown away. `score.league_premium` publishes it as a
**headline finding**: output per deflated euro at the same squad value and
season, relative to the Bundesliga.

| League | Output per euro vs Bundesliga | Club-seasons |
|---|---|---|
| Ligue 1 | 1.08 | 116 |
| La Liga | 1.02 | 119 |
| Bundesliga | 1.00 | 109 |
| Serie A | 0.79 | 133 |
| **Premier League** | **0.42** | 138 |

Premier League clubs get about 42% of the signing output per euro that
comparable Bundesliga clubs get. The raw, unadjusted medians
(`median_output_per_spend`) tell the same story: 0.11 in the Premier League
against 0.32–0.53 elsewhere.

**2. Three-season horizon.** Club ranks under two and three seasons correlate
at rho 0.95. Three better captures development signings, which this project
cares about: Brighton, Leipzig and Gladbach buy young and often loan out. The
cost: **the 2022/23 and 2023/24 cohorts are provisional** (`is_provisional`,
194 club-seasons) and must be flagged in any trend visualisation.

**3. Undisclosed-fee output excluded from the score.** This applies the scoping
doc's existing rule, "excluded and documented, not guessed". Counting output
from signings whose cost is unknown would turn the disclosure gap into fake
efficiency: including it lifted Chelsea by +0.27 and Liverpool by +0.24.
`n_undisclosed` and `output_undisclosed` stay on every club-season for context.

### Sensitivity (measured before deciding)

| Change | Club rank correlation with the chosen spec |
|---|---|
| Horizon 2 instead of 3 | 0.95 |
| Count undisclosed output | 0.98 |
| Weight sub-900-minute seasons 0.25 instead of 0 | 0.99 |
| Log offset 0.5 instead of 0.1 | 0.98 |
| Without league effects | 0.55 |

The first four were measured with the two-season, season-effects-only
prototype. The last compares with and without league effects.

### Results (load of 2026-09-14)

`sql/31_check_recruitment_roi.sql` passes 32 of 32.

- **Credit rule, cohorts and floor:** rebuilt in SQL. The same arrival is
  credited for every one of 11,459 player-seasons, and output, spend, the floor
  and the provisional flags all agree to 1e-9.
- **Regression:** SQL solves it by alternating projections (demean by season,
  then by league, repeated until converged to below 1e-12), a different method
  from R's `lm()`. The slope, residuals, z-scores and league effects agree to
  1e-9.

Eye test, complete cohorts 2017/18–2021/22 (clubs with 3+ scored cohorts):

- **Top:** Montpellier +1.33, Strasbourg +1.09, Real Sociedad +1.03, Eibar,
  Nantes, Alavés, Eintracht Frankfurt, Crystal Palace, Liverpool +0.74.
- **Bottom:** Barcelona −1.60 (the Coutinho, Dembélé and Griezmann fees), Köln,
  Atlético, Burnley, Bournemouth, Juventus, Monaco, Real Madrid.
- **Big clubs:** Liverpool +0.74, Arsenal +0.65, Bayern +0.53, Manchester City
  +0.39, Chelsea −0.14, PSG −0.44, Juventus −0.81, Barcelona −1.60.
- **Brighton by cohort:** −0.93 (2017/18), −0.36, +0.78, +0.68, −0.25, then
  provisional +0.88 and +0.19. That is the "got smarter" arc that the
  two-season, season-effects-only version hid.

`roi_z` correlates with same-season `ppm_z` at only 0.05. Recruitment
efficiency and league results are nearly independent at the club-season level,
which is why the composite's weights are to be derived rather than assumed.

*Revisit criterion:* RB Leipzig (−0.50) and Villarreal (−0.46) rank low here.
If they rank near the top on trading profit and value growth (Pillars 2 and 3),
that confirms the pillars are measuring different strategies, as intended. If
they rank low everywhere, re-examine how loans out are credited.
