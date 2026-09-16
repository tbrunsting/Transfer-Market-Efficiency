# Presentation layer for Power BI: dashboard-shaped views over the warehouse and the scores.
#
# Runs sql/70_presentation_views.sql (drops and recreates schema `presentation`), then reconciles every
# view back to the tables it summarises with sql/71_check_presentation_views.sql.
#
# Run LAST, after the scoring scripts (10 to 60): those rebuild the score tables with DROP ... CASCADE,
# which removes these views.
#
# Run from the repository root:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\70_presentation_views.R

source("R/db.R")

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

stopifnot("the composite has been built" =
            dbGetQuery(con, "SELECT to_regclass('score.club_season_efficiency') IS NOT NULL AS ok")$ok)

invisible(dbWithTransaction(con, run_sql_file(con, "sql/70_presentation_views.sql")))

views <- dbGetQuery(con, "
  SELECT table_name AS view, (SELECT count(*) FROM information_schema.columns c
                              WHERE c.table_schema = v.table_schema AND c.table_name = v.table_name) AS columns
  FROM information_schema.views v WHERE table_schema = 'presentation' ORDER BY table_name")
cat("created", nrow(views), "views in schema presentation:\n")
print(views, row.names = FALSE)

cat("\nchecking against the source tables (sql/71_check_presentation_views.sql):\n")
run_checks(con, "sql/71_check_presentation_views.sql")
