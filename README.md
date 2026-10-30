# RouteGap

Open route + airline reliability data for Indian domestic aviation.

## What runs automatically

Every day at 08:47 IST, GitHub Actions (`.github/workflows/daily_ingest.yml`):

1. checks which [adsb.lol](https://github.com/adsblol/globe_history) days are new (they publish about 1 day behind)
2. downloads each new day (~4 GB), keeps only Indian-registered aircraft over India, and cuts them into flights
3. commits the flights to `data/legs/YYYY-MM/legs_YYYY-MM-DD.csv`
4. adds one line per day to `data/coverage_log.csv` (how many flights we saw, how many had both ends)
5. uploads the raw position points to a monthly release called `points-YYYY-MM`

If a day is missed, the next run catches up (up to 4 days per run).

## Schedules (AeroDataBox)

`.github/workflows/daily_schedules.yml` runs every day at 06:07 IST and saves yesterday's
departure boards (scheduled + actual times) for the 10 DGCA airports to
`data/schedules/YYYY-MM/schedules_YYYY-MM-DD.csv`. That's 20 API calls a day.

To switch it on: GitHub → **Settings → Secrets and variables → Actions → New repository secret**,
name `AERODATABOX_KEY`, value = your RapidAPI key. Optional **Variables**: `AIRPORTS`
(e.g. `DEL,BOM,BLR`) and `AERODATABOX_HOST`. Without the key the job does nothing.

## Setup (one time)

1. Create a **public** GitHub repo and push this folder to it. Public repos get GitHub Actions for free.
2. On GitHub: **Settings → Actions → General → Workflow permissions →** choose **Read and write permissions** → Save.
3. Go to the **Actions** tab, open **Daily ADS-B ingest**, and press **Run workflow** once to test it.

To start from a different day, edit `START_DATE` in `scripts/run_daily.py`.

## Scripts

| Script | What it does |
| --- | --- |
| `scripts/filter_day.py` | One adsb.lol day → Indian aircraft points over India (Parquet) |
| `scripts/build_legs.py` | Points → flights: origin, destination, times, airline, grade |
| `scripts/run_daily.py` | Runs both for every new day; used by GitHub Actions |
| `scripts/capture_schedules.py` | Yesterday's departure boards from AeroDataBox |

Run by hand:

```
pip install -r requirements.txt
python scripts/filter_day.py day.tar.aa day.tar.ab day.tar.ac -o india.parquet
python scripts/build_legs.py india.parquet --airports airports.csv -o legs.csv
```

## Data rules

- Every number shows its source, period and sample size.
- Load factor is never invented.
- A scheduled flight we didn't see is **"not observed"**, never "cancelled".
- Times marked `inferred` are estimates (back-calculated from climb or descent).

## Credits

Flight data © [adsb.lol](https://adsb.lol) contributors, licensed under the
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
Airport data from [OurAirports](https://ourairports.com/data/) (public domain).
