# Phase 3 — the scoring layer

R reads the warehouse, computes each part of the efficiency score, and writes the
results to the `score` schema. Every R result is recomputed independently in
SQL, and a run fails unless the two agree.

| Script | Writes | Verified by |
|---|---|---|
| `R/10_player_quality.R` | `score.player_quality`, `score.player_quality_metric` | `sql/11_check_player_quality.sql` |
| `R/20_sporting_return.R` | `score.club_season_sporting` (Pillar 4) | `sql/21_check_sporting_return.sql` |
| `R/30_recruitment_roi.R` | `score.club_season_recruitment`, `score.signing_credit`, `score.league_premium`, `score.run_parameter` (Pillar 1) | `sql/31_check_recruitment_roi.sql` |
| `R/40_trading_profit.R` | `score.club_season_trading`, `score.sale_basis`, `score.run_parameter` (Pillar 2) | `sql/41_check_trading_profit.sql` |
| `R/50_value_growth.R` | `score.club_season_value_growth`, `score.player_holding`, `score.holding_season_growth` (Pillar 3) | `sql/51_check_value_growth.sql` |
| `R/60_composite.R` | `score.club_season_efficiency`, `score.composite_weight` (the index) | `sql/61_check_composite.sql` |

Run from the repository root, after any warehouse load (the score tables hold
the warehouse's surrogate keys, which a reload renumbers):

```
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\10_player_quality.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\20_sporting_return.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\30_recruitment_roi.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\40_trading_profit.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\50_value_growth.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\60_composite.R
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

*Checked 2026-09-14, after Pillar 2 was built:* RB Leipzig ranks second on
trading profit (+1.13), and the club-level correlation between Pillars 1 and 2
is −0.13. The pillars capture different strategies, as intended. Villarreal is
+0.14 on Pillar 2, so the Pillar 3 check still applies to them.

## 4. Pillar 2: trading profit (scoping doc 5)

One row per club-season: realised profit on that season's sales, meaning sale
price against the player's market value when the club acquired him. Every sale
is in `score.sale_basis`, with the arrival it was linked to, the date and
valuation used for its basis, and the route taken.

### What grounding found before any code was written

1. **"No arrival recorded" was not "academy graduate".** The biggest such
   sales were players bought before 2017 whose purchases are missing from the
   frozen transfers table: Hazard's €120.8m sale, Diego Costa, Nainggolan,
   Drinkwater and Rodrigo. The earlier rule, academy sales as pure income,
   would have booked all of Hazard's fee as profit.
2. **The spell bridge had the loan-out gap Pillar 1 had.** Morata, Cunha, Tomori
   and Romero were loaned out and later sold. The loan closed the spell, so each
   sale looked like it had no purchase basis.
3. **The valuations' club field is unreliable.** See Known limitations below.
   Pillar 2 matches valuations by player and date only, so it is unaffected.

### Decisions (Tyler, 2026-09-14)

**D1. Realised profit only.** Sale price minus basis is mostly value growth
while the player was held: about 88% growth and 12% price achieved above
market value at exit, for sales where both valuations exist. Pillar 2 takes
the *realised* part (players who were sold), and Pillar 3 will take the
*unrealised* part (players still held). Nothing is counted twice.

**D2. The purchase basis.**

- **Signed in the window:** the latest arrival into the club before the sale,
  excluding loan returns, owns the sale. Loans out do not end ownership, so
  Morata's 2020/21 Chelsea sale links to the 2017 purchase through his loan to
  Atlético. The basis is his market value at that arrival.
- **Already at the club when the window opened:** priced *as if acquired at
  market value on 1 July 2017*. This covers both pre-2017 purchases (Hazard is
  priced at his €75m value then, so +€45.8m on the sale) and academy players,
  and never touches the incomplete pre-2017 transfer data. `fact_transfer`
  starts on 1 July 2017, so "no earlier arrival" means exactly "at the club
  when the window opened".
- **Valuation lookup:** the latest valuation up to 365 days before the basis
  date. Failing that, the first up to 180 days after, because young signings
  are often valued only after joining (145 sales). Failing both, 0 (97 sales,
  mostly youth).

**D3. Scale.** Profit can be negative and is heavy-tailed, so a log is not
possible:

- Raw profit let single club-seasons reach z = 7.7.
- A signed log (asinh) over-compressed: Monaco fell from +1.33 to −0.14.
- Adopted: **profit in that season's median fees, winsorised at the 1st and
  99th percentiles of club-seasons** (−9.8 to +24.7 median fees; 14 capped,
  including Monaco 2018/19 at +50). It keeps money linear, and club ranks
  correlate with raw at rho 0.99.

The result then gets the Pillar 1 normalisation: residual on log squad value,
with season and league effects.

**Defaults approved:**

- **Undisclosed-fee sales** are excluded from income (1,100, scoping doc 7) and
  counted per club-season.
- **Loan fees received** count as income at zero basis (744 fees, €1.0bn).
- **Only sales made in a club's big-five seasons count.**

### League effects

Capped profit in median fees per club-season, relative to the Bundesliga, at
the same squad value and season. Recorded in `score.run_parameter`.

| League | Effect |
|---|---|
| Ligue 1 | +1.89 |
| Serie A | +0.95 |
| Bundesliga | 0 |
| La Liga | −0.26 |
| Premier League | −0.55 |

Ligue 1 is the selling league: its clubs realise the most profit for their
size. This is a natural companion to Pillar 1's league premium (the Premier
League buys expensively, and Ligue 1 sells well), and is worth a line in the
write-up.

### Results (load of 2026-09-14)

`sql/41_check_trading_profit.sql` passes 26 of 26:

- **Every sale:** owning arrival, basis source, basis date, valuation used,
  lookup route, basis and profit are rebuilt in SQL using `LATERAL` lookups.
- **Every club-season total:** matches.
- **Winsor bounds and capping:** match.
- **Normalisation:** alternating projections again.

All agree to 1e-9. The checks were also tested against planted errors (a basis
off by €1, a wrong owning arrival, a z-score off by 0.001), and each was caught.

| Fee sales by basis source | Sales | Income | Basis |
|---|---|---|---|
| Signed in the window | 1,488 | €15.6bn | €9.8bn |
| At club on 1 July 2017 | 1,232 | €12.1bn | €8.5bn |
| Loan fees received | 744 | €1.0bn | 0 |

- **Largest profits:** Mbappé (Monaco, +€145m on a €35m 2017 basis), Neymar
  (Barcelona, +€122m), Declan Rice (West Ham, +€116m on his €0.5m academy
  promotion), Bellingham (Dortmund), Dembélé (Dortmund), Grealish (Villa),
  Caicedo (Brighton).
- **Largest losses:** Griezmann (Barcelona, −€108m against his €130m value at
  signing), Cristiano Ronaldo (Juventus, −€83m), Suárez, Coutinho.

Eye test (clubs with 4+ big-five seasons):

- **Top:** Lille +1.75, RB Leipzig +1.13, Atalanta +1.12, Brighton +1.07,
  Dortmund +1.03, Leicester, Real Madrid, Monaco, Rennes.
- **Bottom:** Bayern −1.43, PSG −1.11, Arsenal, Manchester United, Marseille,
  Milan, Tottenham, Napoli, Barcelona.

Profit z correlates with same-season points per match at −0.03, and negatively with
every success measure once aggregated — see section 6, "selling well costs points". The club-level
correlation with Pillar 1 is −0.13: buying well and selling well are different
skills, which supports deriving the composite's weights.

## 5. Pillar 3: squad value growth (scoping doc 5)

One row per club-season: the value the club's own players gained or lost while
it still held them, marked to market each season. Realised profit on players
who were sold is Pillar 2; nothing is counted twice.

### Ownership is built from transfers, never from the valuation's club field

Pillar 3 needs to know **which club held a player on a date**, which is exactly
what the valuation club field cannot say (Known limitations, below). So
ownership is rebuilt from transfer events:

- a permanent move (fee, free, undisclosed) passes ownership to the buying club;
- a loan does not: the asset stays with the owner, wherever the player plays;
- a loan return proves who the owner was, and is taken as authoritative;
- before a player's first event, the owner is that event's origin club (its
  destination if that event is a loan return);
- same-day events are ordered loan return, then permanent move, then loan out.
  Most dates are estimated, so without that rule they sort arbitrarily: Zapata's
  end-of-loan and his permanent move to Sampdoria share a date. Ordering them
  cut ownership contradictions from 2,406 to 661.

So the owner at any moment is the destination of the latest non-loan event on
or before it. **This explains 99% of the minutes in the warehouse** (89.6%
played by players the club owned, 9.4% by loanees), with 1.0% unexplained.

Measured against it, the valuation club field is right for only **73.4% of
valuations (87% by value)**, 67.5% in 2017/18, and per club it misassigns −12%
to +22% of value. Pillar 3 sums value per club directly, so that error would
have landed straight in the score. It is not used.

### Which spells are scored

14,235 ownership spells at in-scope clubs, of which 8,563 are scored:

| Outcome | Spells | Treatment |
|---|---|---|
| Still held on 1 July 2024 | 3,757 | scored to the closing value |
| Free departure | 3,986 | scored, written off to 0 |
| Ended in a fee sale Pillar 2 counted | 2,404 | excluded: Pillar 2 has the whole spell |
| Undisclosed departure | 1,666 | excluded: unknown proceeds |
| No end valuation | 1,584 | excluded: nothing to measure |
| Fee sale outside a big-five season | 791 | scored: Pillar 2 never counted it |

**A spell is excluded when a Pillar 2 sale falls anywhere inside it**, not only
exactly at its end. The first version keyed on the ending event and let five
spells (Knockaert, Afobe, Rolán, Lammers, Ciervo) into both pillars: their
same-day estimated dates put another club's loan return after the sale, so the
spell appeared to end with a loan return. A check enforces the invariant
directly.

The 1,584 spells with no end valuation are fringe and youth players: 872 never
played a big-five minute for the club and 597 were never valued at all
(decided with Tyler: drop them, record the count).

### How growth is measured

Each scored spell is marked to market in every season its club spent in the big
five (`score.holding_season_growth`):

- the first season starts at the acquisition value, which for a player already
  at the club is his value on 1 July 2017, the same convention as Pillar 2;
- season boundaries use the last known valuation, so a stale valuation carries
  forward instead of dropping a player to zero mid-spell;
- **a free departure writes the remaining value off to zero.** Losing an asset
  for nothing is a real outcome: Messi (€80m, Barcelona 2021), Donnarumma
  (€60m), Alaba (€55m), Škriniar (€50m), Pogba (€48m). Across the window,
  2,378 players left for nothing, taking €6.6bn of value with them. Including
  write-offs or not correlates at rho 0.94 on club ranks, so this is not a
  marginal call: excluding them would flatter exactly the clubs that lost stars
  free (Milan +0.47, Arsenal +0.45, Schalke +0.33, Juventus +0.23).

Growth is measured against **value at acquisition, not the fee paid** (decided
2026-09-14). Overpaying is already penalised by Pillar 1, so using fees here
would count the same mistake twice.

Scale is the same as Pillar 2: growth in that season's median fees, winsorised
at the 1st and 99th percentiles (−28.5 to +30.0 median fees, 14 capped), then
the residual on log squad value with season and league effects.

### Pillar 3 is thinner in early seasons, by design

By 2018/19, **92% of that season's value growth belongs to players who have
since been sold**, and their whole story is in Pillar 2. In 2023/24 it is 2%.

| Season | Growth scored here | Growth excluded (sold later, Pillar 2) | Excluded share |
|---|---|---|---|
| 2017/18 | €1,931m | €3,730m | 66% |
| 2018/19 | €333m | €3,907m | 92% |
| 2020/21 | −€478m | €1,358m | 74% |
| 2023/24 | €1,532m | €28m | 2% |

This is the realised/unrealised split working, not a gap: a club's development
work appears in Pillar 2 once the player is sold, and in Pillar 3 while it is
still on the books. It does mean **Pillar 3 measures less in early seasons**,
and a trend visualisation should not read the early figures as weak
performance. The alternative (re-basing Pillar 2 to the value at the start of
the sale season) was considered and rejected: it would reopen verified work to
fix something this pillar can state honestly (decided with Tyler, 2026-09-16).

### Results (load of 2026-09-14)

`sql/51_check_value_growth.sql` passes 22 of 22. It rebuilds ownership, the
values at both ends, the exclusion rules, every season mark, the club-season
totals, the winsorising and the normalisation (alternating projections again),
and agrees with R to 1e-9. The checks were also tested against planted errors —
a scored spell that Pillar 2 already counted, a season value off by €1,000, a
residual off by 0.01 — and each was caught by the right check.

Season totals show the market itself: +€2.2bn in 2017/18, **−€3.0bn in
2019/20** as COVID hit valuations, and +€1.6bn in 2023/24.

Eye test (clubs with 4+ big-five seasons):

- **Top:** Real Sociedad +1.16, Leverkusen +0.92, Manchester City +0.85,
  Atalanta +0.75, Aston Villa +0.71, Bayern +0.69, Stuttgart, Brighton, Lille.
- **Bottom:** Juventus −1.41, Manchester United −0.97, Sevilla −0.77,
  Tottenham, Everton, Atlético, Schalke, Marseille, Chelsea.
- **Biggest single-season write-offs:** Chelsea 2022/23 €89m (Rüdiger,
  Christensen), Barcelona 2021/22 €82m (Messi), Arsenal 2021/22 €81m, Liverpool
  2022/23 €73m, Manchester United 2022/23 €73m (Pogba).

### The four pillars together

Club means, for clubs with five or more seasons:

| Club | Recruitment | Trading | Value growth | Points |
|---|---|---|---|---|
| Manchester City | +0.39 | +0.20 | +0.85 | +2.02 |
| Bayern Munich | +0.53 | −1.43 | +0.69 | +1.98 |
| Real Sociedad | +1.03 | −0.24 | +1.16 | +0.40 |
| Lille | −0.15 | +1.75 | +0.57 | +0.75 |
| Brighton | −0.01 | +1.07 | +0.60 | −0.37 |
| Barcelona | −1.60 | −0.60 | −0.01 | +1.92 |
| Juventus | −0.81 | +0.24 | −1.41 | +1.45 |
| Manchester United | +0.20 | −0.88 | −0.97 | +0.85 |

Pillar correlations are all weak (|r| ≤ 0.20): recruitment and trading −0.13,
recruitment and growth +0.20, trading and growth +0.19, and none of them
tracks league points closely. The pillars measure genuinely different things,
which is what the composite's derived weights need.

*Villarreal check (Pillar 1's revisit criterion):* −0.46 recruitment, +0.14
trading, +0.11 growth. Middling rather than low everywhere, so no change to how
loans out are credited.

## 6. The composite efficiency index

One number per club-season, from the four pillars:

| Pillar | Weight | Source |
|---|---|---|
| Recruitment ROI | 0.30 | `score.club_season_recruitment.roi_z` |
| Trading profit | 0.30 | `score.club_season_trading.profit_z` |
| Value growth | 0.30 | `score.club_season_value_growth.growth_z` |
| Sporting return | 0.10 | `score.club_season_sporting.ppm_z` |

The weights live in `score.composite_weight`, with their rationale, rather than
buried in code; the SQL check reads them from there instead of repeating them.

### The weights are chosen, not derived — and why that changed

The scoping doc (section 5) said the weights would be **derived**: regress each
pillar against sporting success and let the coefficients decide, so that "the
model is answerable to the data rather than to the analyst's priors". Measured
against this data, that method fails three ways.

**1. Wrong signs.** Trading profit takes a *negative* coefficient against every
success target tried. At club level, so does recruitment ROI:

| Pillar | vs points | vs points above squad-value expectation | vs trophies |
|---|---|---|---|
| Recruitment ROI | −0.198 | −0.099 | −0.167 |
| Trading profit | −0.103 | −0.005 | −0.202 |
| Value growth | +0.039 | +0.201 | +0.081 |

Derived weights would therefore *subtract* good recruitment and good trading
from an efficiency index. 

**2. Almost no signal.** Regressing the three money pillars on each target:

| Target | R² | Coefficients (recruit / trade / growth) |
|---|---|---|
| Points per match (z in league-season) | 0.044 | +0.040 / −0.051 / +0.191 |
| Points above squad-value expectation | 0.129 | +0.023 / −0.026 / +0.103 |
| Won a trophy | 0.016 | −0.016 / −0.021 / +0.033 |
| Next season's points | 0.006 | −0.072 / +0.017 / −0.005 |

**3. Unstable.** Leaving one season out moves the coefficients as much as the
coefficients themselves: value growth ranges +0.08 to +0.28 against points,
trading −0.10 to 0.00.

This is **structural, not a bad choice of target**. Every pillar is residualised
against squad value by design, so it measures performance relative to club size.
League points are 0.67 correlated with squad value. Efficiency and success are
therefore partly opposed, and no regression of one on the other can produce
sensible weights. Clipping the negative coefficients to zero "fixes" the signs
but collapses the index onto value growth alone (0.81 of the weight), which
defeats having four pillars, and it is a result forced by the analyst rather
than found in the data.

**Decision (Tyler, 2026-09-16): equal weights on the three money pillars, with
sporting return as a small guard rail at 0.10.** Equal weights are defensible
here: the money pillars are near independent (|r| ≤ 0.20), each is already
standardised, and nothing in the evidence ranks one above another. The 0.10 on
sporting return does the job the scoping doc gives Pillar 4 — stopping
"efficient" from meaning cheap and bad — at the smallest weight that achieves it:

| Weight on sporting return | Club rank correlation vs money-only | Average points z of the top 10 | Top 3 |
|---|---|---|---|
| 0.00 | 1.00 | +0.23 | Lille, Brighton, Real Sociedad |
| **0.10** | **0.96** | **+0.72** | **Lille, Atalanta, Manchester City** |
| 0.25 | 0.70 | +1.20 | Manchester City, Lille, Atalanta |
| 0.50 | 0.24 | +1.58 | Manchester City, Liverpool, Real Madrid |

At 0.25 and above the index turns into a quality ranking. At 0.10 it stays an
efficiency index — and FC Empoli (points z −0.67), the one cheap-and-bad club
that reached the money-only top ten, drops out.

This is the scoping doc's own standard applied to itself: an honestly documented
wrong answer beats a clean one that hides its assumptions. Section 5 of the
scoping doc is amended accordingly.

### A finding, not just an obstacle: selling well costs points

Trading profit's negative coefficient against every success measure is worth
stating in its own right. **Selling your best players at a profit is nearly the
same act as weakening your squad in the short term.** The clubs that realise the
most value — Lille, Leipzig, Atalanta, Brighton, Dortmund — are doing something
that shows up as money this season and, often, as fewer points. That tension is
the reason the index needs several pillars rather than one: a club can be
excellent at trading and mediocre at results in the same season, and both facts
are true.

### Club-seasons without a recruitment score

69 club-seasons fall below the recruitment spend floor and have no Pillar 1
score. Rather than leaving them unscored, the remaining weights are
renormalised: the index is the weighted mean of the pillars the club-season
actually has (weight applied 0.70 instead of 1.00), and
`is_recruitment_missing` flags it.

`is_provisional` is inherited from Pillar 1: the 2022/23 and 2023/24 signing
cohorts carry three seasons of credited output, which runs past the scored
window. 194 club-seasons are flagged, and any trend must show them as
provisional.

### Verification

`sql/61_check_composite.sql` passes 14 of 14. It rebuilds the index from the four
pillar tables using the stored weights, and checks the properties the index is
supposed to have: every club-season present, renormalisation exactly where
recruitment is missing, provisional flags inherited, no pillar inert, and the
index not collapsing into a league table or a size ranking. Agreement with R is
to 1e-9, and planted errors (a nudged index, a flipped provisional flag, a
changed weight) were each caught.

An R operator-precedence bug was caught here by the schema, not by a test: in R
`!` binds looser than `*`, so `z * weight * !missing + ...` negates the whole sum
rather than the flag, and every index came out as 0. The NOT NULL constraint on
`efficiency_z` rejected the load before a single row was written.

### Results (load of 2026-09-14)

The index correlates **0.32 with league points and 0.13 with squad value**: it is
neither a disguised league table nor a disguised rich list.

**Most efficient clubs** (4+ big-five seasons, mean z):

| Club | Index | Recruitment | Trading | Growth | Points |
|---|---|---|---|---|---|
| Lille | +1.45 | +0.02 | +1.75 | +0.57 | +0.75 |
| Atalanta | +1.13 | −0.14 | +1.12 | +0.75 | +0.84 |
| Manchester City | +1.11 | +0.26 | +0.20 | +0.85 | +2.02 |
| Real Sociedad | +0.97 | +0.76 | −0.24 | +1.16 | +0.40 |
| Brighton | +0.95 | +0.14 | +1.07 | +0.60 | −0.37 |
| Stuttgart | +0.81 | +0.60 | +0.28 | +0.63 | −0.23 |
| Liverpool | +0.81 | +0.62 | +0.18 | +0.10 | +1.59 |

**Least efficient:** Schalke −0.84, Manchester United −0.81, Juventus −0.78,
PSG −0.73, Marseille −0.65, Köln −0.58, Burnley −0.56, Barcelona −0.46.

Note how differently the top clubs get there: Lille and Brighton through
trading, Real Sociedad through recruitment and growth, Manchester City through
growth and results. PSG and Barcelona sit at the bottom despite being among the
best teams in Europe by points (+2.17 and +1.92) — which is exactly what an
efficiency index, rather than a quality ranking, should show.

**Who got smarter** (complete cohorts only, first three seasons vs later ones):
Udinese, Arsenal, Köln, Real Madrid, Brighton and Stuttgart improved most;
Juventus, Barcelona, Lyon, Atlético, Leicester and Bayern declined most. Because
the provisional cohorts are excluded, the "later" era here is thin — in places a
single season — so the dashboard should show the trend season by season with the
provisional flag rather than lean on a two-era summary.

## Known limitations (scoring layer)

### The valuations' club field is not the club at that date

`fact_player_valuation.club_key` comes from transfermarkt-datasets'
`current_club_id`. Grounding Pillar 2 showed it is often the player's later or
latest club, not the club on the valuation date. Every Eden Hazard valuation,
including 2008 (Lille) and 2013 (Chelsea), says Real Madrid, and Verratti's
2009–11 valuations say PSG while he was at Pescara.

Measured on 22,267 valuations dated September–April for players with one
big-five club that season, the field matches the club actually played for
**85.5% of the time (93.5% by value)**, rising from 75.5% in 2017/18 to 90.1% in
2022/23.

**What it affects.** `fact_club_season.squad_value_start_eur` assigns players to
clubs with this field. Squad value is the scale control in Pillars 1 and 2.

**Tested impact.** Pillar 1's normalisation was re-run with a squad value that
ignores the field (the valuation of players who actually played for the club).

- Club rank correlation is 0.94, and club-season correlation is 0.96–0.98 in
  every season, so the time gradient does not leak into the scores.
- Individual clubs move by up to ±0.47 (Sampdoria, Levante, PSG, Marseille).

**Not affected.** Every market value used by Pillars 1 and 2 (the bridge values
at arrival and exit, and the Pillar 2 basis) is matched by player and date only.

**Decision (Tyler, 2026-09-14):** documented, not rebuilt.

**Pillar 3 (checked 2026-09-16).** Value growth does need to know which club
held a player on a date, so the field was measured against ownership rebuilt
from transfer events: it agrees for only **73.4% of valuations (87% by value)**,
67.5% in 2017/18, and per club it misassigns −12% to +22% of value. Pillar 3
therefore builds ownership from transfers and never reads this field. See
Pillar 3 above.

*Revisit criterion:* if the composite or a club page depends on squad value
more directly than as a log-scale control, rebuild squad value from
club-at-date membership first.
