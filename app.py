import glob
import pandas as pd
import streamlit as st

st.set_page_config(page_title="RouteGap", page_icon="✈️")

# ---------- load all the daily flight files into one table ----------
files = glob.glob("data/legs/*/legs_*.csv")
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

# keep only airline flights where we know both ends
df = df[df["airline"].notna()]
df = df[df["grade"].isin(["complete", "complete_inferred"])]
df["route"] = df["origin"] + "-" + df["dest"]

days = len(files)

# ---------- page ----------
st.title("✈️ RouteGap")
st.write("Open data on Indian domestic flights, built from ADS-B signals.")
st.caption(f"Source: adsb.lol (ODbL) · {days} days of data · {len(df)} flights with both ends known")

# 1. flights per airline
st.header("Flights per airline")
airline_counts = df["airline"].value_counts()
st.bar_chart(airline_counts)

# 2. busiest routes
st.header("Busiest routes")
route_counts = df["route"].value_counts().head(20)
st.bar_chart(route_counts)

# 3. pick a route and see who flies it
st.header("Who flies this route?")
route = st.selectbox("Choose a route", route_counts.index)
on_route = df[df["route"] == route]
share = on_route["airline"].value_counts()

st.write(f"**{route}**: {len(on_route)} flights seen, flown by {len(share)} airline(s)")
st.bar_chart(share)

if len(share) == 1:
    st.warning("Only one airline flies this route: a competition gap.")