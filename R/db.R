# Shared by every scoring script: the warehouse connection and a SQL-file runner.
#
# Credentials come from .env (PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD), the same file the Python
# loader reads. libpq picks them up from the environment, so no credential ever appears in R code.

suppressPackageStartupMessages({
  library(DBI)
  library(RPostgres)
})

if (!file.exists(".env")) stop("run from the repository root: .env not found in ", getwd())

warehouse_connect <- function() {
  readRenviron(".env")
  con <- dbConnect(RPostgres::Postgres())
  db <- dbGetQuery(con, "SELECT current_database() AS db")$db
  if (db != "transfer_market") {
    dbDisconnect(con)
    stop("connected to '", db, "', expected transfer_market")
  }
  dbExecute(con, "SET client_min_messages = warning")   # no NOTICE chatter from IF NOT EXISTS
  con
}

# Runs a whole .sql file as one simple-protocol call, so it may hold several statements.
run_sql_file <- function(con, path) {
  sql <- paste(readLines(path, encoding = "UTF-8", warn = FALSE), collapse = "\n")
  invisible(dbExecute(con, sql, immediate = TRUE))
}

# Runs a checks file (check_name, expected, actual, status) and stops if anything is not PASS.
run_checks <- function(con, path) {
  sql <- paste(readLines(path, encoding = "UTF-8", warn = FALSE), collapse = "\n")
  res <- dbGetQuery(con, sql)
  old <- options(width = 200)
  on.exit(options(old))
  print(res, row.names = FALSE, right = FALSE)
  bad <- res[res$status != "PASS", ]
  if (nrow(bad)) stop(nrow(bad), " check(s) in ", path, " did not pass")
  cat("\nverdicts:", nrow(res), "PASS\n")
  invisible(res)
}
