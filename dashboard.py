# coding:UTF-8
# Web dashboard to review tilt-log measurements.
#
#   streamlit run dashboard.py
#
# Pick a CSV from data/ (or upload one) and view summary stats, time series,
# the slope distribution, and the sway spectrum (FFT). Analysis only -- it does
# not talk to the sensor.

import glob
import os
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import analyze_tilt

DATA_DIR = "data"
SLOPE_THRESHOLD_PCT = 0.1  # 0.1% = 1/1000 alarm threshold


def list_csvs():
    return sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))


@st.cache_data
def read_csv(path, _mtime):
    # _mtime busts the cache when the file changes.
    return pd.read_csv(path)


st.set_page_config(page_title="Tilt Log Dashboard", layout="wide")
st.sidebar.title("Tilt Log")

# --- Choose a data source: a file from data/ or an upload ---
files = list_csvs()
selected = st.sidebar.selectbox(
    "Files in data/", files, index=len(files) - 1,
    format_func=os.path.basename) if files else None
uploaded = st.sidebar.file_uploader("or upload a CSV", type=["csv"])
st.sidebar.caption("Threshold: {0}% (1/1000)".format(SLOPE_THRESHOLD_PCT))

path = None
if uploaded is not None:
    path = os.path.join(tempfile.gettempdir(), uploaded.name)
    with open(path, "wb") as f:
        f.write(uploaded.getbuffer())
elif selected:
    path = selected

if path is None:
    st.info("No data found. Run a measurement (python log_tilt.py) or upload a CSV.")
    st.stop()

df = read_csv(path, os.path.getmtime(path))
label = os.path.basename(path)
st.title("Tilt Log Analysis")
st.caption(label)

# --- Derived series ---
t = df["elapsed_s"].to_numpy()
roll = df["Roll_deg"].to_numpy()
pitch = df["Pitch_deg"].to_numpy()
slope = df["slope_pct"].to_numpy()
n = len(df)
dur = float(t[-1] - t[0]) if n > 1 else 0.0
fs = (n - 1) / dur if dur > 0 else 25.0

# --- Summary metrics ---
cols = st.columns(6)
cols[0].metric("samples", n)
cols[1].metric("rate (Hz)", "{0:.1f}".format(fs))
cols[2].metric("Roll sigma (deg)", "{0:.4f}".format(np.nanstd(roll)))
cols[3].metric("Pitch sigma (deg)", "{0:.4f}".format(np.nanstd(pitch)))
cols[4].metric("slope mean (%)", "{0:.4f}".format(np.nanmean(slope)))
cols[5].metric("slope max (%)", "{0:.4f}".format(np.nanmax(slope)))

over = np.count_nonzero(slope > SLOPE_THRESHOLD_PCT)
if over:
    st.warning("slope exceeded {0}% in {1} of {2} samples ({3:.1f}%)".format(
        SLOPE_THRESHOLD_PCT, over, n, 100.0 * over / n))
else:
    st.success("slope stayed below the {0}% threshold for the whole record".format(SLOPE_THRESHOLD_PCT))

# --- Time series (angles + slope on a secondary axis) ---
ts = make_subplots(specs=[[{"secondary_y": True}]])
ts.add_trace(go.Scatter(x=t, y=roll, name="Roll", line=dict(width=1)), secondary_y=False)
ts.add_trace(go.Scatter(x=t, y=pitch, name="Pitch", line=dict(width=1)), secondary_y=False)
ts.add_trace(go.Scatter(x=t, y=slope, name="slope %", line=dict(width=1, color="crimson")),
             secondary_y=True)
ts.add_hline(y=SLOPE_THRESHOLD_PCT, line=dict(color="red", dash="dash"),
             secondary_y=True, annotation_text="{0}%".format(SLOPE_THRESHOLD_PCT))
ts.update_xaxes(title_text="elapsed (s)")
ts.update_yaxes(title_text="angle (deg)", secondary_y=False)
ts.update_yaxes(title_text="slope (%)", secondary_y=True)
ts.update_layout(height=420, margin=dict(t=30, b=10), legend=dict(orientation="h"))
st.subheader("Time series")
st.plotly_chart(ts, use_container_width=True)

left, right = st.columns(2)

# --- Slope distribution ---
with left:
    st.subheader("Slope distribution")
    hist = go.Figure(go.Histogram(x=slope[~np.isnan(slope)], nbinsx=60))
    hist.update_layout(height=350, margin=dict(t=10), xaxis_title="slope (%)", yaxis_title="count")
    st.plotly_chart(hist, use_container_width=True)

# --- Sway spectrum (FFT of slope) ---
with right:
    st.subheader("Sway spectrum (FFT of slope)")
    s = slope[~np.isnan(slope)].astype(float)
    s = s - s.mean()
    spec = go.Figure()
    if len(s) > 1:
        window = np.hanning(len(s))
        amp = np.abs(np.fft.rfft(s * window))
        freq = np.fft.rfftfreq(len(s), d=1.0 / fs)
        spec.add_trace(go.Scatter(x=freq, y=amp, line=dict(width=1)))
        peak = freq[1:][np.argmax(amp[1:])] if len(amp) > 2 else 0.0
        spec.update_layout(title_text="dominant ~ {0:.3f} Hz".format(peak))
    spec.update_xaxes(title_text="frequency (Hz)", range=[0, min(5.0, fs / 2)])
    spec.update_yaxes(title_text="amplitude")
    spec.update_layout(height=350, margin=dict(t=30))
    st.plotly_chart(spec, use_container_width=True)

# --- Optional raw channels ---
with st.expander("Gyro and temperature"):
    extra = make_subplots(specs=[[{"secondary_y": True}]])
    for ax in ("GyroX_dps", "GyroY_dps", "GyroZ_dps"):
        if ax in df:
            extra.add_trace(go.Scatter(x=t, y=df[ax].to_numpy(), name=ax, line=dict(width=1)),
                            secondary_y=False)
    if "Temp_C" in df:
        extra.add_trace(go.Scatter(x=t, y=df["Temp_C"].to_numpy(), name="Temp",
                                   line=dict(width=1.5, color="orange")), secondary_y=True)
    extra.update_xaxes(title_text="elapsed (s)")
    extra.update_yaxes(title_text="gyro (deg/s)", secondary_y=False)
    extra.update_yaxes(title_text="temp (C)", secondary_y=True)
    extra.update_layout(height=350, margin=dict(t=10), legend=dict(orientation="h"))
    st.plotly_chart(extra, use_container_width=True)

# --- Full text report (reuses analyze_tilt) ---
with st.expander("Full analysis report"):
    st.code(analyze_tilt.analyze(path))
