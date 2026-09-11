r"""
Phase 1, Step 1: Can we talk to FBref at all?

This is a deliberately tiny test. It makes ONE request (the Big 5 league
index page) and prints what came back. The point is to fail fast: if
Cloudflare blocks the scraper, we find out in 30 seconds instead of
discovering it 40 minutes into a full season pull.

Run:  .venv\Scripts\python scripts\01_test_connection.py
"""

import time

import soccerdata as sd

t0 = time.perf_counter()

# "Big 5 European Leagues Combined" is FBref's own combined page for all five
# leagues. Using it means 1 request per season instead of 5 -- see notes below.
fbref = sd.FBref(
    leagues="Big 5 European Leagues Combined",
    seasons="2017-2018",
)

print(f"Reader built in {time.perf_counter() - t0:.1f}s")
print(f"Cache dir: {fbref.data_dir}")
print(f"Rate limit: {fbref.rate_limit}s between requests")

t1 = time.perf_counter()
leagues = fbref.read_leagues()
print(f"\nread_leagues() took {time.perf_counter() - t1:.1f}s")
print(leagues)

t2 = time.perf_counter()
seasons = fbref.read_seasons()
print(f"\nread_seasons() took {time.perf_counter() - t2:.1f}s")
print(seasons)

print(f"\nTOTAL: {time.perf_counter() - t0:.1f}s")
