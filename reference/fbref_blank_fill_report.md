# Blank player-seasons filled from Kaggle

Written by `scripts/13_fill_blank_players_from_kaggle.py`. The frozen snapshot is never modified; these values
are a correction table applied on load (`reference/fbref_blank_fill.csv`).

Blank rows with minutes in 2017/18-2023/24: 31. Filled: 19. Values written: 384.
Of the 13 player-seasons with 450+ minutes: 13 filled, 0 not filled.

## Proof: clubs whose player sum equals the team file

| Metric | Before | After | Team-seasons |
|---|---|---|---|
| standard.PrgP_Progression | 669 | 681 | 684 |
| standard.PrgC_Progression | 670 | 683 | 684 |
| possession.PrgC_Carries | 670 | 683 | 684 |
| defense.Int | 684 | 684 | 684 |
| defense.Clr | 665 | 682 | 684 |
| defense.Tkl_Tackles | 665 | 680 | 684 |

## Column pairs (used if agreement >= 97%)

| Kaggle column | FBref file.column | Agreement | Players compared | Used |
|---|---|---|---|---|
| Expected Goals | standard.xG_Expected | 99.57% | 18,205 | yes |
| Exp NPG | standard.npxG_Expected | 99.61% | 18,205 | yes |
| Progressive Passes | standard.PrgP_Progression | 99.85% | 18,205 | yes |
| Progressive Carries | standard.PrgC_Progression | 99.80% | 18,205 | yes |
| Expected Goals | shooting.xG_Expected | 99.57% | 18,205 | yes |
| Exp NPG | shooting.npxG_Expected | 99.61% | 18,205 | yes |
| Total Shots | shooting.Sh_Standard | 99.88% | 18,216 | yes |
| % Shots on target | shooting.SoT_percent_Standard | 99.87% | 15,613 | yes |
| Goals per shot | shooting.G_per_Sh_Standard | 99.99% | 15,613 | yes |
| Goals per shot on target | shooting.G_per_SoT_Standard | 99.99% | 13,385 | yes |
| Tackles attempted | defense.Tkl_Tackles | 99.86% | 18,205 | yes |
| Tackles Won | defense.TklW_Tackles | 99.87% | 18,216 | yes |
| % Dribbles tackled | defense.Tkl_percent_Challenges | 86.76% | 16,928 | no |
| Shots blocked | defense.Sh_Blocks | 99.98% | 18,205 | yes |
| Passes blocked | defense.Pass_Blocks | 99.88% | 18,205 | yes |
| Interceptions | defense.Int | 99.87% | 18,216 | yes |
| Clearances | defense.Clr | 99.86% | 18,205 | yes |
| Errors made | defense.Err | 99.90% | 18,205 | yes |
| touches_def_pen | possession.Def Pen_Touches | 99.87% | 18,205 | yes |
| Take ons attempted | possession.Att_Take | 99.49% | 18,205 | yes |
| % Successful take-ons | possession.Succ_percent_Take | 99.43% | 16,097 | yes |
| Times tackled during take-on | possession.Tkld_Take | 20.88% | 18,205 | no |
| carries_prgc | possession.PrgC_Carries | 99.80% | 18,205 | yes |
| carries final 3rd | possession.Final_Third_Carries | 99.82% | 18,205 | yes |
| carries penalty area | possession.CPA_Carries | 99.90% | 18,205 | yes |
| Possessions lost | possession.Dis_Carries | 99.90% | 18,205 | yes |
| Possessions lost | possession.Mis_Carries | 12.08% | 18,205 | no |
| Passes Completed | passing.Cmp_Total | 34.06% | 18,209 | no |
| Passes Attempted | passing.Att_Total | 33.58% | 18,209 | no |
| Pass completion % | passing.Cmp_percent_Total | 31.54% | 18,209 | no |
| Progressive passes distance | passing.PrgDist_Total | 29.00% | 18,209 | no |
| % Short pass completed | passing.Cmp_percent_Short | 34.16% | 18,193 | no |
| % Medium passes completed | passing.Cmp_percent_Medium | 32.34% | 18,195 | no |
| % Long passes completed | passing.Cmp_percent_Long | 32.75% | 18,172 | no |
| Key passes | passing.KP | 61.52% | 18,209 | no |
| 1/3 | passing.Final_Third | 40.03% | 18,209 | no |
| Passes into penalty area | passing.PPA | 57.60% | 18,209 | no |
| % Aerial Duels won | misc.Won_percent_Aerial | 99.82% | 17,497 | yes |
| Goals Against | keepers.GA | 86.66% | 1,372 | no |
| Saves | keepers.Saves | 86.17% | 1,374 | no |
| Saves % | keepers.Save_percent | 86.10% | 1,367 | no |
| Clean Sheets | keepers.CS | 88.39% | 1,370 | no |
| % Clean sheets | keepers.CS_percent | 88.10% | 1,344 | no |
| Crosses Stopped | keepers_adv.Stp_Crosses | 11.95% | 1,372 | no |

## Player-seasons with 450+ minutes

| Season | Club | Player | Minutes | Filled values |
|---|---|---|---|---|
| 2022/23 | Mallorca | 이강인 | 2,823 | 19 |
| 2022/23 | Angers | Valery | 2,497 | 19 |
| 2022/23 | Lecce | Gabriel Strefezza | 2,442 | 19 |
| 2022/23 | Toulouse | Thijs Dallinga | 2,353 | 19 |
| 2022/23 | Brighton | Robert Sanchez | 2,070 | 22 |
| 2019/20 | Sampdoria | Ronaldo Vieira | 1,819 | 19 |
| 2022/23 | Valencia | Hugo Guillamón | 1,632 | 19 |
| 2021/22 | Lille | Leonardo César Jardim | 1,530 | 22 |
| 2022/23 | Wolfsburg | Omar Marmoush | 1,476 | 19 |
| 2022/23 | Sampdoria | Ronaldo Vieira | 815 | 19 |
| 2018/19 | Sampdoria | Ronaldo Vieira | 599 | 19 |
| 2022/23 | Lille | Leonardo César Jardim | 540 | 22 |
| 2021/22 | Sampdoria | Ronaldo Vieira | 495 | 20 |

## Still blank for these rows (known gap)

Columns with no Kaggle counterpart, or whose counterpart failed the agreement test, stay blank for filled rows:

- standard.xAG_Expected
- misc.Won_Aerial
- possession.Touches_Touches
- keepers_adv.PSxG_Expected
- defense.Tkl_percent_Challenges (86.8% agreement)
- possession.Tkld_Take (20.9% agreement)
- possession.Mis_Carries (12.1% agreement)
- passing.Cmp_Total (34.1% agreement)
- passing.Att_Total (33.6% agreement)
- passing.Cmp_percent_Total (31.5% agreement)
- passing.PrgDist_Total (29.0% agreement)
- passing.Cmp_percent_Short (34.2% agreement)
- passing.Cmp_percent_Medium (32.3% agreement)
- passing.Cmp_percent_Long (32.7% agreement)
- passing.KP (61.5% agreement)
- passing.Final_Third (40.0% agreement)
- passing.PPA (57.6% agreement)
- keepers.GA (86.7% agreement)
- keepers.Saves (86.2% agreement)
- keepers.Save_percent (86.1% agreement)
- keepers.CS (88.4% agreement)
- keepers.CS_percent (88.1% agreement)
- keepers_adv.Stp_Crosses (12.0% agreement)
