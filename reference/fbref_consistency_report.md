# FBref consistency report

Written by `scripts/12_check_fbref_consistency.py`. Checks the rules in
`docs/phase-1-data-coverage.md` against the frozen snapshot for the scored window
(2017/18-2023/24). Re-running on unchanged data produces this file unchanged.

Manifest SHA-256: `4e4588b1e95de4dd921ecfc5e888df8304698b84b6a936ac426d8d68db71afae`

**Result: ALL CHECKS PASSED** (34 passed, 0 failed, 7 informational)

| Rule | Check | Status | Detail |
|---|---|---|---|
| frozen data | checksums of every file read | PASS | 28 files hashed, 0 mismatched |
| 1 | big5_player_standard | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_shooting | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_passing | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_playing_time | PASS | lowest season max-90s 38.0, lowest key-column fill 100.0% |
| 1 | big5_player_defense | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_possession | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_misc | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_keepers | PASS | lowest season max-90s 38.0, lowest key-column fill 99.5% |
| 1 | big5_player_keepers_adv | PASS | lowest season max-90s 38.0, lowest key-column fill 98.6% |
| 1 | players with 450+ minutes but blank advanced stats (known gap) | INFO | 13 player-seasons, 21,091 minutes: 2022/23 Mallorca 이강인 (2823 min); 2022/23 Angers Valery (2497 min); 2022/23 Lecce Gabriel Strefezza (2442 min); 2022/23 Toulouse Thijs Dallinga (2353 min); 2022/23 Brighton Robert Sanchez (2070 min); 2019/20 Sampdoria Ronaldo Vieira (1819 min); 2022/23 Valencia Hugo Guillamón (1632 min); 2021/22 Lille Leonardo César Jardim (1530 min); 2022/23 Wolfsburg Omar Marmoush (1476 min); 2022/23 Sampdoria Ronaldo Vieira (815 min); 2018/19 Sampdoria Ronaldo Vieira (599 min); 2022/23 Lille Leonardo César Jardim (540 min); 2021/22 Sampdoria Ronaldo Vieira (495 min). Blank in every advanced file; filled from Kaggle where columns validate (13_fill_blank_players_from_kaggle.py) |
| 1 | big5_team_standard | PASS | one row per club in every season |
| 1 | big5_team_passing | PASS | one row per club in every season |
| 1 | big5_team_gca | PASS | one row per club in every season |
| 1 | big5_team_defense | PASS | one row per club in every season |
| 1 | big5_team_possession | PASS | one row per club in every season |
| 1 | team standard: full seasons | PASS | 2017/18: 38, 2018/19: 38, 2019/20: 38, 2020/21: 38, 2021/22: 38, 2022/23: 38, 2023/24: 38 matches (max per club) |
| 1 | Kaggle SCA per 90 | PASS | 7 seasons, lowest fill 100.0% |
| 2 | progressive passes: standard file vs big5_team_passing | PASS | teams matching exactly, lowest season 99% (teams with a blank player value left out: 31 team-seasons) |
| 2 | progressive passes: standard file vs big5_team_standard | PASS | teams matching exactly, lowest season 99% (teams with a blank player value left out: 31 team-seasons) |
| 2 | passing file progressive passes (known issue) | INFO | older data version in 2017/18, 2018/19, 2019/20, 2020/21, 2021/22; never use it for progressive passes |
| 3 | goals: team = sum of players | PASS | teams matching exactly, lowest season 100% (teams with a blank player value left out: 0 team-seasons) |
| 3 | penalties attempted: team = sum of players | PASS | teams matching exactly, lowest season 100% (teams with a blank player value left out: 0 team-seasons) |
| 3 | progressive carries: team = sum of players | PASS | teams matching exactly, lowest season 99% (teams with a blank player value left out: 31 team-seasons) |
| 3 | tackles + interceptions: team = sum of players | PASS | teams matching exactly, lowest season 100% (teams with a blank player value left out: 31 team-seasons) |
| 3 | xG: player sum vs FBref team xG (known discrepancy) | PASS | player sums above team figures by +1.45% to +2.10% per season. Team xG is built from player sums; never mix with FBref's team xG |
| 5 | player rows resolve to a team ID via (season, club name) | PASS | 19,563 of 19,563 (100.00%); (season, club name) unique: True |
| 5 | distinct clubs in the window | PASS | 145 team IDs (expected 145) |
| 5 | player IDs standing for more than one person | PASS | 4acd733a in 2022/23: 56 league matches at Angers/Girona/Southampton (known: Valery Fernandez (Girona) and Yan Valery (Southampton, Angers)) |
| 5 | clubs whose name changes inside the snapshot | INFO | 32f3ee20: M'Gladbach -> Gladbach |
| 4 | Kaggle 2017/18 | PASS | minutes matched 99.86%; same xG/PrgP/Int 100.0/100.0/100.0%; team SCA gap -0.06%, clubs within 1% 99% |
| 4 | Kaggle 2018/19 | PASS | minutes matched 99.87%; same xG/PrgP/Int 99.8/100.0/100.0%; team SCA gap -0.13%, clubs within 1% 100% |
| 4 | Kaggle 2019/20 | PASS | minutes matched 99.83%; same xG/PrgP/Int 100.0/100.0/100.0%; team SCA gap -0.17%, clubs within 1% 98% |
| 4 | Kaggle 2020/21 | PASS | minutes matched 99.76%; same xG/PrgP/Int 100.0/100.0/100.0%; team SCA gap -0.16%, clubs within 1% 93% |
| 4 | Kaggle 2021/22 | PASS | minutes matched 99.77%; same xG/PrgP/Int 99.9/100.0/100.0%; team SCA gap -0.16%, clubs within 1% 95% |
| 4 | Kaggle 2022/23 | PASS | minutes matched 99.72%; same xG/PrgP/Int 97.5/99.7/99.9%; team SCA gap -0.32%, clubs within 1% 94% |
| 4 | Kaggle 2023/24 | PASS | minutes matched 99.70%; same xG/PrgP/Int 99.5/98.9/99.3%; team SCA gap -0.30%, clubs within 1% 92% |
| 4 | club names that differ between Kaggle and FBref (learned, not assumed) | INFO | Kaggle 'Gladbach' = FBref 'M'Gladbach' |
| 4 | fingerprint links whose names share no word (review by eye) | INFO | 13 of 164: 2020/21 Valladolid: FBref 'Maranhão' = Kaggle 'Marcos André'; 2020/21 Betis: FBref 'Rodrigo' = Kaggle 'Rodri'; 2021/22 Valencia: FBref 'Maranhão' = Kaggle 'Marcos André'; 2021/22 Betis: FBref 'Rodrigo' = Kaggle 'Rodri'; 2022/23 Almería: FBref 'Juan Brandáriz' = Kaggle 'Chumi'; 2022/23 Espanyol: FBref 'Wassim Boullif' = Kaggle 'Simo'; 2022/23 Sevilla: FBref 'Manuel Sebastián' = Kaggle 'Manu Bueno'; 2022/23 Lyon: FBref 'Jefferson' = Kaggle 'Jeffinho'; 2022/23 Valencia: FBref 'Maranhão' = Kaggle 'Marcos André'; 2023/24 Las Palmas: FBref 'Francisco Jesús Crespo García' = Kaggle 'Pejiño'; 2023/24 Almería: FBref 'Juan Brandáriz' = Kaggle 'Chumi'; 2023/24 Sevilla: FBref 'Manuel Sebastián' = Kaggle 'Manu Bueno'; 2023/24 Lens: FBref 'Abduqodir Xusanov' = Kaggle 'Abdukodir Khusanov' |
| 4 | links for rows with blank advanced stats (review by eye) | INFO | 8: 2021/22 Lille: FBref 'Leonardo César Jardim' = Kaggle 'Léo Jardim'; 2022/23 Mallorca: FBref '이강인' = Kaggle 'Lee Kang-in'; 2022/23 Southampton: FBref 'Valery' = Kaggle 'Yan Valery'; 2022/23 Angers: FBref 'Valery' = Kaggle 'Yan Valery'; 2022/23 Girona: FBref 'Casals' = Kaggle 'Joel Roca'; 2022/23 Lille: FBref 'Leonardo César Jardim' = Kaggle 'Léo Jardim'; 2022/23 Brighton: FBref 'Robert Sanchez' = Kaggle 'Robert Sánchez'; 2022/23 Cádiz: FBref 'Blanco' = Kaggle 'Antonio Blanco' |
| 4 | crosswalk stored | INFO | 18,224 links (18,052 by name, 164 by fingerprint, 8 blank-row) -> reference/kaggle_fbref_crosswalk.csv |
