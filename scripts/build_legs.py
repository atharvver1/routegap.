"""
build_legs.py  -  RouteGap Step 0 pilot

Takes the Parquet file made by filter_day.py and cuts each aircraft's points
into flights ("legs"). For every leg it finds:
  - origin and destination airport (nearest airport to where the leg starts/ends low)
  - wheels-off and touchdown time (UTC)
  - callsign and airline
  - a grade: complete (both ends seen), partial (one end), cruise_only (neither)

Usage:
    python build_legs.py india_2026-09-18.parquet --airports airports_world.csv -o legs_2026-09-18.csv
"""

import argparse

import numpy as np
import pandas as pd

LOW_FT = 3000          # below this, an aircraft is "near an airport"
MAX_AIRPORT_KM = 15    # how close the low point must be to an airport
SPLIT_GAP_MIN = 30     # a gap this long while low/on the ground = a new leg

# When an end of the flight wasn't seen low, we infer it (see find_end):
INFER_FT = 12000       # first/last seen below this height...
INFER_KM = 60          # ...within this distance of an airport
CLIMB_FPM = 2000       # assumed average climb rate after takeoff (feet per minute) - an ESTIMATE
DESCENT_FPM = 1000     # assumed average descent rate before landing - an ESTIMATE

AIRLINES = {           # ICAO callsign prefix -> (airline, IATA code)
    "IGO": ("IndiGo", "6E"),
    "AIC": ("Air India", "AI"),
    "AXB": ("Air India Express", "IX"),
    "AKJ": ("Akasa Air", "QP"),
    "SEJ": ("SpiceJet", "SG"),
    "LLR": ("Alliance Air", "9I"),
    "SDG": ("Star Air", "S5"),
}


def load_airports(path):
    a = pd.read_csv(path)
    a = a[(a["iso_country"] == "IN") & a["iata_code"].notna()
          & a["type"].isin(["large_airport", "medium_airport", "small_airport"])]
    a = a[["iata_code", "type", "scheduled_service", "latitude_deg", "longitude_deg", "elevation_ft"]]
    a = a.reset_index(drop=True)
    a["elevation_ft"] = a["elevation_ft"].fillna(0)
    return a


def distances_km(lat, lon, airports):
    """Haversine distance (km) from one point to every airport."""
    lat1, lon1 = np.radians(lat), np.radians(lon)
    lat2 = np.radians(airports["latitude_deg"].to_numpy())
    lon2 = np.radians(airports["longitude_deg"].to_numpy())
    return 2 * 6371 * np.arcsin(np.sqrt(np.sin((lat2 - lat1) / 2) ** 2
                                        + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2))


def nearest_airport(lat, lon, airports, max_km):
    """Returns (IATA code, elevation in ft) of the nearest airport, or (None, None) if it's further than max_km."""
    d = distances_km(lat, lon, airports)
    i = d.argmin()
    if d[i] > max_km:
        return None, None
    return airports["iata_code"].iloc[i], airports["elevation_ft"].iloc[i]


def likely_airports(lat, lon, airports, max_km):
    """For an INFERRED end: every airport with scheduled service within max_km.

    Returns (best guess, elevation, all candidates as 'DEL|HDO').
    Best guess = nearest large/medium airport, so a busy airport beats a small one
    nearby (Delhi beats Hindon). The schedule decides for sure later.
    """
    d = distances_km(lat, lon, airports)
    near = airports[(d <= max_km) & (airports["scheduled_service"] == "yes")].copy()
    if near.empty:
        return None, None, None
    near["km"] = d[near.index]
    near["small"] = near["type"] == "small_airport"
    near = near.sort_values(["small", "km"])
    best = near.iloc[0]
    candidates = "|".join(near.sort_values("km")["iata_code"])
    return best["iata_code"], best["elevation_ft"], candidates


def is_low(row):
    return row["on_ground"] or (pd.notna(row["alt_ft"]) and row["alt_ft"] < LOW_FT)


def split_into_legs(points):
    """Gives each point of ONE aircraft a leg number.

    A new leg starts when readsb flags one, or after a long gap where the aircraft
    was below 15,000 ft on both sides (it landed, sat on the ground unseen, took off).
    A long gap at cruise height is just a hole in coverage, so it does NOT split.
    """
    below = points["on_ground"] | (points["alt_ft"] < 15000)
    gap_min = points["time_utc"].diff().dt.total_seconds() / 60
    turnaround = (gap_min > SPLIT_GAP_MIN) & below & below.shift(fill_value=False)
    new_leg = points["new_leg"] | turnaround
    return new_leg.cumsum()


def altitude_change(leg, from_start, seconds=120):
    """How many feet the aircraft climbed (+) or descended (-) over its first/last 2 minutes."""
    air = leg[leg["alt_ft"].notna()]
    if len(air) < 2:
        return 0
    if from_start:
        window = air[air["time_utc"] <= air["time_utc"].iloc[0] + pd.Timedelta(seconds=seconds)]
        return window["alt_ft"].iloc[-1] - window["alt_ft"].iloc[0]
    window = air[air["time_utc"] >= air["time_utc"].iloc[-1] - pd.Timedelta(seconds=seconds)]
    return window["alt_ft"].iloc[-1] - window["alt_ft"].iloc[0]


def find_end(leg, airports, at_start):
    """Works out the airport and time at one end of a leg.

    Returns (airport, time_utc, method, candidates):
      method 'seen'     - the aircraft was seen on the ground or below 3,000 ft near the airport
      method 'inferred' - it was first/last seen climbing/descending below 12,000 ft
                          within 60 km of an airport; the time is back-calculated (an ESTIMATE)
      candidates        - every airport it could be ('DEL|HDO'); the schedule settles it later
    """
    nothing = (None, None, None, None)
    airborne = leg[~leg["on_ground"]]
    point = leg.iloc[0] if at_start else leg.iloc[-1]
    edge = airborne.iloc[0] if at_start else airborne.iloc[-1]

    if is_low(point):
        code, _ = nearest_airport(point["lat"], point["lon"], airports, MAX_AIRPORT_KM)
        if code:
            return code, edge["time_utc"], "seen", code

    alt = edge["alt_ft"]
    if pd.isna(alt) or alt >= INFER_FT:
        return nothing
    change = altitude_change(leg, from_start=at_start)
    heading_right_way = change > 300 if at_start else change < -300
    if not heading_right_way:
        return nothing

    code, elevation, candidates = likely_airports(edge["lat"], edge["lon"], airports, INFER_KM)
    if not code:
        return nothing
    height = max(alt - elevation, 0)
    if at_start:
        minutes = height / CLIMB_FPM
        return code, edge["time_utc"] - pd.Timedelta(minutes=minutes), "inferred", candidates
    minutes = height / DESCENT_FPM
    return code, edge["time_utc"] + pd.Timedelta(minutes=minutes), "inferred", candidates


def summarise_leg(leg, airports):
    airborne = leg[~leg["on_ground"]]
    if airborne.empty:                       # only taxiing, not a flight
        return None

    origin, wheels_off, origin_method, origin_candidates = find_end(leg, airports, at_start=True)
    dest, touchdown, dest_method, dest_candidates = find_end(leg, airports, at_start=False)

    callsigns = leg["callsign"].dropna()
    callsign = callsigns.mode().iloc[0] if not callsigns.empty else None
    prefix = callsign[:3] if callsign else None
    airline, iata = AIRLINES.get(prefix, (None, None))

    if origin and dest and origin != dest:
        both_seen = origin_method == "seen" and dest_method == "seen"
        grade = "complete" if both_seen else "complete_inferred"
    elif origin or dest:
        grade = "partial"
    else:
        grade = "cruise_only"

    first = leg.iloc[0]
    return {
        "hex": first["hex"],
        "reg": first["reg"],
        "type": first["type"],
        "callsign": callsign,
        "airline": airline,
        "flight_no": iata + callsign[3:] if iata and callsign else None,
        "origin": origin,
        "origin_method": origin_method,
        "origin_candidates": origin_candidates,
        "wheels_off_utc": wheels_off,
        "dest": dest,
        "dest_method": dest_method,
        "dest_candidates": dest_candidates,
        "touchdown_utc": touchdown,
        "first_seen_alt_ft": airborne["alt_ft"].iloc[0],
        "last_seen_alt_ft": airborne["alt_ft"].iloc[-1],
        "ground_points": int(leg["on_ground"].sum()),
        "max_alt_ft": airborne["alt_ft"].max(),
        "n_points": len(leg),
        "grade": grade,
    }


def main():
    parser = argparse.ArgumentParser(description="Cut one day of Indian ADS-B points into flights")
    parser.add_argument("points", help="parquet file from filter_day.py")
    parser.add_argument("--airports", required=True, help="OurAirports airports.csv")
    parser.add_argument("-o", "--output", required=True, help="output legs .csv")
    args = parser.parse_args()

    airports = load_airports(args.airports)
    df = pd.read_parquet(args.points).sort_values(["hex", "time_utc"]).reset_index(drop=True)

    legs = []
    for hex_code, points in df.groupby("hex"):
        points = points.reset_index(drop=True)
        points["leg"] = split_into_legs(points)
        for _, leg in points.groupby("leg"):
            summary = summarise_leg(leg.reset_index(drop=True), airports)
            if summary:
                legs.append(summary)

    legs = pd.DataFrame(legs)
    legs.to_csv(args.output, index=False)

    # ---- pilot report ----
    airline_legs = legs[legs["airline"].notna()]
    print("Legs found (all Indian aircraft):", len(legs))
    print("Legs by Indian airlines         :", len(airline_legs))
    print("\nGrade of airline legs (%):")
    print((airline_legs["grade"].value_counts(normalize=True) * 100).round(1).to_string())
    print("\nAirline legs by airline and grade:")
    print(pd.crosstab(airline_legs["airline"], airline_legs["grade"], margins=True).to_string())
    known = airline_legs[airline_legs["grade"].isin(["complete", "complete_inferred"])]
    print("\nDistinct routes with both ends known:", known.groupby(["origin", "dest"]).ngroups)
    print("\nTop 15 routes (both ends known):")
    print(known.groupby(["origin", "dest"]).size().sort_values(ascending=False).head(15).to_string())
    print("\nHow each airport's departures and arrivals were found (airline legs):")
    dep = airline_legs.groupby(["origin", "origin_method"]).size().unstack(fill_value=0)
    arr = airline_legs.groupby(["dest", "dest_method"]).size().unstack(fill_value=0)
    table = dep.add_prefix("dep_").join(arr.add_prefix("arr_"), how="outer").fillna(0).astype(int)
    table["total"] = table.sum(axis=1)
    print(table.sort_values("total", ascending=False).head(20).to_string())
    print("\nSaved to:", args.output)


if __name__ == "__main__":
    main()
