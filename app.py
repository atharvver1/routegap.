import glob
import pandas as pd
import streamlit as st

st.set_page_config(page_title="RouteGap", page_icon="✈️")

# ---------- load all the daily flight files into one table ----------
files = sorted(glob.glob("data/legs/*/legs_*.csv"))
tables = []
for f in files:
    one_day = pd.read_csv(f)
    one_day["day"] = pd.to_datetime(f[-14:-4])     # the date is in the file name
    tables.append(one_day)
everything = pd.concat(tables, ignore_index=True)

# airline flights only
everything = everything[everything["airline"].notna()]

# times are saved in UTC; show them in Indian time (IST)
for col in ["wheels_off_utc", "touchdown_utc"]:
    everything[col] = pd.to_datetime(everything[col], utc=True, format="mixed")
    everything[col] = everything[col].dt.tz_convert("Asia/Kolkata")

# for the charts: only flights where we know both ends
df = everything[everything["grade"].isin(["complete", "complete_inferred"])].copy()
df["route"] = df["origin"] + "-" + df["dest"]

days = len(files)
first_day = files[0][-14:-4]      # "legs_2026-10-01.csv" -> "2026-10-01"
last_day = files[-1][-14:-4]

# ---------- page ----------
st.title("✈️ RouteGap")
st.write("Open data on Indian domestic flights, built from ADS-B signals.")
st.caption(f"Source: adsb.lol (ODbL) · {days} days, {first_day} to {last_day} · "
           f"{len(df)} flights with both ends known")

tab_overview, tab_find = st.tabs(["Overview", "Find a flight"])

# =====================================================================
with tab_overview:

    # 1. flights per airline
    st.header("Flights per airline")
    st.bar_chart(df["airline"].value_counts())

    # 2. busiest routes
    st.header("Busiest routes")
    route_counts = df["route"].value_counts()
    st.bar_chart(route_counts.head(20))
    st.caption(f"Top 20 of {len(route_counts)} routes seen.")

    # 3. pick an airport and see where it flies
    st.header("Routes from an airport")
    airports = sorted(df["origin"].unique())
    airport = st.selectbox("Choose an airport", airports, index=airports.index("DEL"))
    from_airport = df[df["origin"] == airport]
    st.write(f"**{airport}**: {len(from_airport)} departures seen to "
             f"{from_airport['dest'].nunique()} airports")
    st.bar_chart(from_airport["dest"].value_counts())

    # 4. pick a route and see who flies it
    st.header("Who flies this route?")
    route = st.selectbox("Choose a route (type to search)", route_counts.index)
    on_route = df[df["route"] == route]
    share = on_route["airline"].value_counts()

    st.write(f"**{route}**: {len(on_route)} flights seen, flown by {len(share)} airline(s)")
    st.bar_chart(share)

    if len(share) == 1:
        st.warning("Only one airline flies this route: a competition gap.")
    if len(on_route) < 60:
        st.info(f"Only {len(on_route)} flights seen. Treat these numbers as rough.")

    # 5. what we can't see yet
    st.header("Coverage")
    st.write(
        "We only count a flight when a volunteer ADS-B receiver saw it near both airports. "
        "Big airports in the south and west (BOM, BLR, HYD, MAA) are well covered. "
        "Some airports, like Indore (IDR), have no receiver nearby yet, so their flights "
        "don't appear here. That doesn't mean they aren't flying."
    )
    st.caption(f"Airports seen so far: {df['origin'].nunique()}")

# =====================================================================
with tab_find:

    st.header("Find a flight")
    st.write("Search the flights we observed, by flight number, callsign or aircraft registration.")

    search_by = st.selectbox("Search by", ["Flight number", "Callsign", "Registration"])
    examples = {"Flight number": "e.g. 6E2134", "Callsign": "e.g. IGO2134", "Registration": "e.g. VT-IJK"}
    columns = {"Flight number": "flight_no", "Callsign": "callsign", "Registration": "reg"}

    text = st.text_input("Search", placeholder=examples[search_by])
    pick_date = st.checkbox("Pick a date")
    day = st.date_input("Date") if pick_date else None

    if text:
        # ignore spaces and capital letters: "6e 2134" finds "6E2134"
        wanted = text.replace(" ", "").upper()
        col = everything[columns[search_by]].fillna("").str.replace(" ", "").str.upper()
        found = everything[col == wanted]

        if day:
            found = found[found["day"].dt.date == day]

        if found.empty:
            st.warning("No flights found. We may not have seen it: some airports have no "
                       "receiver nearby, and our data starts on " + first_day + ".")
        else:
            st.success(f"{len(found)} flight(s) found")
            table = pd.DataFrame({
                "Date": found["day"],
                "Flight": found["flight_no"],
                "Airline": found["airline"],
                "From": found["origin"].fillna("?"),
                "To": found["dest"].fillna("?"),
                "Took off (IST)": found["wheels_off_utc"].dt.strftime("%H:%M"),
                "Landed (IST)": found["touchdown_utc"].dt.strftime("%H:%M"),
                "Aircraft": found["reg"].fillna("") + " " + found["type"].fillna(""),
                "Tracking": found["grade"].str.replace("_", " "),
            })
            table = table.sort_values("Date", ascending=False)
            table["Date"] = table["Date"].dt.strftime("%d %b %Y")
            table = table.fillna("not seen")
            st.dataframe(table, hide_index=True)
            st.caption("Times are when the aircraft left or reached the runway, not the gate. "
                       "'complete inferred' means one end was estimated from the climb or descent. "
                       "'?' or 'not seen' means no receiver saw that end of the flight.")
