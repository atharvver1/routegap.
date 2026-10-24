"""
filter_day.py  -  RouteGap Step 0 pilot

Reads ONE day of adsb.lol history (the split tar files from the
adsblol/globe_history_2026 GitHub release) and keeps only:
  - aircraft registered in India (ICAO hex 800000 to 83FFFF, the VT- block)
  - position points inside a box around India

It saves the result as one small Parquet file you can open with pandas or DuckDB.

The 4 GB tar is read as a stream, file by file, so nothing is extracted to disk.

Usage (works on Windows, Mac and Linux):
    python filter_day.py v2026.09.18-planes-readsb-prod-0.tar.aa v2026.09.18-planes-readsb-prod-0.tar.ab -o india_2026-09-18.parquet
"""

import argparse
import gzip
import json
import tarfile

import pandas as pd

# Indian-registered aircraft use ICAO 24-bit addresses 800000-83FFFF
INDIA_HEX_FIRST = 0x800000
INDIA_HEX_LAST = 0x83FFFF

# Rough box around India (degrees)
LAT_MIN, LAT_MAX = 6.0, 37.0
LON_MIN, LON_MAX = 68.0, 98.0


class JoinedFiles:
    """Makes several files (tar.aa, tar.ab, ...) look like one long file."""

    def __init__(self, paths):
        self.files = [open(p, "rb") for p in paths]
        self.index = 0

    def read(self, size=-1):
        chunks = []
        while self.index < len(self.files) and (size < 0 or size > 0):
            data = self.files[self.index].read(size)
            if not data:                      # this part is finished, go to the next
                self.files[self.index].close()
                self.index += 1
                continue
            chunks.append(data)
            if size > 0:
                size -= len(data)
        return b"".join(chunks)


def is_indian_trace(name):
    """True for files like ./traces/3a/trace_full_80063a.json with an Indian hex."""
    filename = name.split("/")[-1]
    if not (filename.startswith("trace_full_") and filename.endswith(".json")):
        return False
    hex_code = filename[len("trace_full_"):-len(".json")]
    if hex_code.startswith("~"):              # '~' marks a non-ICAO (TIS-B) address
        return False
    try:
        number = int(hex_code, 16)
    except ValueError:
        return False
    return INDIA_HEX_FIRST <= number <= INDIA_HEX_LAST


def read_trace(raw_bytes):
    """Trace files are gzipped JSON. Handles plain JSON too, just in case."""
    if raw_bytes[:2] == b"\x1f\x8b":
        raw_bytes = gzip.decompress(raw_bytes)
    return json.loads(raw_bytes)


def points_in_india(trace):
    """Turns one aircraft's trace into a list of rows, keeping only points over India."""
    rows = []
    base_time = trace["timestamp"]
    for p in trace.get("trace", []):
        lat, lon = p[1], p[2]
        if lat is None or lon is None:
            continue
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            continue

        altitude = p[3]
        on_ground = altitude == "ground"
        details = p[8] if len(p) > 8 else None
        callsign = None
        if details and details.get("flight"):
            callsign = details["flight"].strip()

        rows.append({
            "hex": trace["icao"],
            "reg": trace.get("r"),
            "type": trace.get("t"),
            "time_utc": base_time + p[0],          # seconds since 1970, UTC
            "lat": lat,
            "lon": lon,
            "alt_ft": None if on_ground else altitude,
            "on_ground": on_ground,
            "speed_kt": p[4],
            "track": p[5],
            "new_leg": bool(p[6] & 2),              # readsb's guess that a new flight starts here
            "callsign": callsign,
            "source": p[9] if len(p) > 9 else None,  # e.g. adsb_icao, mlat
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description="Keep Indian aircraft over India from one adsb.lol day")
    parser.add_argument("parts", nargs="+", help="the .tar.aa, .tar.ab ... files, in order")
    parser.add_argument("-o", "--output", required=True, help="output .parquet file")
    args = parser.parse_args()

    stream = JoinedFiles(args.parts)
    tar = tarfile.open(fileobj=stream, mode="r|")   # 'r|' = read as a stream

    all_rows = []
    aircraft_seen = 0
    files_seen = 0
    for member in tar:
        files_seen += 1
        if not member.isfile() or not is_indian_trace(member.name):
            continue
        raw = tar.extractfile(member).read()
        trace = read_trace(raw)
        rows = points_in_india(trace)
        if rows:
            aircraft_seen += 1
            all_rows.extend(rows)

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("No Indian aircraft found. Check the input files.")
        return

    df["time_utc"] = pd.to_datetime(df["time_utc"], unit="s", utc=True)
    df = df.sort_values(["hex", "time_utc"])

    # The callsign is only sent every so often, so carry it forward within each aircraft.
    # It resets at each new leg so one flight's callsign never leaks into the next.
    df["leg_no"] = df.groupby("hex")["new_leg"].cumsum()
    df["callsign"] = df.groupby(["hex", "leg_no"])["callsign"].transform(lambda s: s.ffill().bfill())

    df.to_parquet(args.output, index=False)

    print("Files in archive      :", files_seen)
    print("Indian aircraft kept  :", aircraft_seen)
    print("Position points kept  :", len(df))
    print("Saved to              :", args.output)


if __name__ == "__main__":
    main()
