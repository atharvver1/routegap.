"""
run_daily.py  -  RouteGap automatic ingest

Runs every day on GitHub Actions (see .github/workflows/daily_ingest.yml):
  1. finds which adsb.lol days are published but not processed yet
  2. for each new day: downloads it, runs filter_day.py and build_legs.py, deletes the download
  3. saves the flights to data/legs/YYYY-MM/legs_YYYY-MM-DD.csv
     and one summary line per day to data/coverage_log.csv

It catches up automatically: if it misses a few days, the next run does them (up to MAX_DAYS per run).

Settings can be changed with environment variables:
    START_DATE   first day to process   (default 2026-10-01)
    MAX_DAYS     days per run           (default 4)
"""

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
LEGS_DIR = ROOT / "data" / "legs"
LOG_FILE = ROOT / "data" / "coverage_log.csv"
AIRPORTS = ROOT / "data" / "airports_world.csv"
OUT_DIR = ROOT / "out"            # points files go here, then get uploaded to a GitHub release
WORK_DIR = ROOT / "work"          # temporary downloads, deleted after each day

START_DATE = date.fromisoformat(os.environ.get("START_DATE", "2026-10-01"))
MAX_DAYS = int(os.environ.get("MAX_DAYS", "4"))


def release_url(day, pod, part):
    tag = f"v{day:%Y.%m.%d}-planes-readsb-{pod}-0"
    return (f"https://github.com/adsblol/globe_history_{day.year}/releases/download/"
            f"{tag}/{tag}.tar.{part}")


def available_days():
    """Days that adsb.lol has published, read from the release tags with 'git ls-remote' (no API needed)."""
    days = set()
    for year in range(START_DATE.year, date.today().year + 1):
        repo = f"https://github.com/adsblol/globe_history_{year}"
        result = subprocess.run(["git", "ls-remote", "--tags", repo], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            tag = line.split("refs/tags/")[-1]
            if tag.endswith("-planes-readsb-prod-0"):
                y, m, d = tag[1:11].split(".")
                days.add(date(int(y), int(m), int(d)))
    return days


def done_days():
    """Days we already have a legs file for."""
    done = set()
    for f in LEGS_DIR.glob("*/legs_*.csv"):
        done.add(date.fromisoformat(f.stem[len("legs_"):]))
    return done


def part_sizes(day, pod):
    """Sizes in bytes of each part (aa, ab, ac...) of one release. Stops at the first missing part."""
    sizes = []
    for part in ["aa", "ab", "ac", "ad", "ae"]:
        request = urllib.request.Request(release_url(day, pod, part), method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                sizes.append(int(response.headers.get("Content-Length", 0)))
        except urllib.error.HTTPError:
            break
    return sizes


def choose_pod(day):
    """adsb.lol publishes 'prod' and 'staging' copies. Use prod unless it's clearly smaller (incomplete)."""
    prod, staging = part_sizes(day, "prod"), part_sizes(day, "staging")
    if staging and sum(staging) > 1.1 * sum(prod):
        return "staging", len(staging)
    return "prod", len(prod)


def download(day, pod, n_parts):
    WORK_DIR.mkdir(exist_ok=True)
    files = []
    for part in ["aa", "ab", "ac", "ad", "ae"][:n_parts]:
        path = WORK_DIR / f"day.tar.{part}"
        subprocess.run(["curl", "-sSL", "--retry", "3", "-o", str(path), release_url(day, pod, part)], check=True)
        files.append(str(path))
    return files


def summarise(day, legs_file, pod):
    """One line for coverage_log.csv, so we can watch coverage over time."""
    legs = pd.read_csv(legs_file)
    airline = legs[legs["airline"].notna()]
    grades = airline["grade"].value_counts()
    known = airline[airline["grade"].isin(["complete", "complete_inferred"])]
    return {
        "date": day.isoformat(),
        "source": pod,
        "airline_legs": len(airline),
        "complete": int(grades.get("complete", 0)),
        "complete_inferred": int(grades.get("complete_inferred", 0)),
        "partial": int(grades.get("partial", 0)),
        "cruise_only": int(grades.get("cruise_only", 0)),
        "routes_both_ends": known.groupby(["origin", "dest"]).ngroups,
    }


def process(day):
    pod, n_parts = choose_pod(day)
    if n_parts == 0:
        print(day, "no files found, skipping")
        return None
    print(day, f"downloading {n_parts} parts from {pod}...", flush=True)
    parts = download(day, pod, n_parts)

    OUT_DIR.mkdir(exist_ok=True)
    points = OUT_DIR / f"india_{day}.parquet"
    month_dir = LEGS_DIR / f"{day:%Y-%m}"
    month_dir.mkdir(parents=True, exist_ok=True)
    legs = month_dir / f"legs_{day}.csv"

    subprocess.run([sys.executable, str(SCRIPTS / "filter_day.py"), *parts, "-o", str(points)], check=True)
    shutil.rmtree(WORK_DIR)          # free the disk before the next day
    subprocess.run([sys.executable, str(SCRIPTS / "build_legs.py"), str(points),
                    "--airports", str(AIRPORTS), "-o", str(legs)], check=True)
    return summarise(day, legs, pod)


def main():
    todo = sorted(d for d in available_days() - done_days() if d >= START_DATE)
    print(f"{len(todo)} new days available; processing up to {MAX_DAYS} this run")

    for day in todo[:MAX_DAYS]:
        row = process(day)
        if row:
            log = pd.DataFrame([row])
            if LOG_FILE.exists():
                log = pd.concat([pd.read_csv(LOG_FILE), log]).drop_duplicates("date", keep="last")
            log.sort_values("date").to_csv(LOG_FILE, index=False)
            print(row)


if __name__ == "__main__":
    main()
