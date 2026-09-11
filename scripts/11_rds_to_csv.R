# Phase 1, step 1b: convert the frozen worldfootballR .rds files to CSV.
#
# Called by 10_freeze_fbref_snapshot.py; you normally don't run it directly.
#
# Why R, and why base R only: .rds is R's own format, so R is the reference
# reader for it. worldfootballR itself is deliberately NOT used -- the package
# was archived in September 2025, so the freeze must not depend on it.
#
# Every season in each file is kept (some go back to 2009/10). Filtering to the
# scored window (2017/18-2023/24) happens later, in SQL staging, so the raw
# layer stays a faithful copy of the source.
#
# Usage: Rscript --vanilla scripts/11_rds_to_csv.R <path to data/raw/fbref>
# Writes: <raw>/csv/<release>/<name>.csv and <raw>/csv/_conversion_log.csv

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 1) stop("usage: Rscript --vanilla 11_rds_to_csv.R <data/raw/fbref>")
raw <- normalizePath(args[1], winslash = "/", mustWork = TRUE)
src_root <- file.path(raw, "worldfootballR")
out_root <- file.path(raw, "csv")

season_label <- function(end_year) {
  if (is.na(end_year)) return("")
  sprintf("%d/%02d", end_year - 1L, end_year %% 100L)
}

files <- sort(list.files(src_root, pattern = "[.]rds$", recursive = TRUE))
if (length(files) == 0) stop("no .rds files under ", src_root)

log <- vector("list", length(files))
for (i in seq_along(files)) {
  f <- files[i]
  d <- as.data.frame(readRDS(file.path(src_root, f)))
  out_rel <- sub("[.]rds$", ".csv", f)
  out <- file.path(out_root, out_rel)
  dir.create(dirname(out), recursive = TRUE, showWarnings = FALSE)
  write.csv(d, out, row.names = FALSE, na = "", fileEncoding = "UTF-8")

  seasons <- if ("Season_End_Year" %in% names(d)) range(d$Season_End_Year, na.rm = TRUE) else c(NA, NA)
  log[[i]] <- data.frame(
    rds = file.path("worldfootballR", f), csv = file.path("csv", out_rel),
    rows = nrow(d), cols = ncol(d),
    first_season = season_label(seasons[1]), last_season = season_label(seasons[2])
  )
  cat(sprintf("  %-58s %7d rows  %s-%s\n", f, nrow(d), log[[i]]$first_season, log[[i]]$last_season))
}

write.csv(do.call(rbind, log), file.path(out_root, "_conversion_log.csv"), row.names = FALSE)
cat(sprintf("converted %d files\n", length(files)))
