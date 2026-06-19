# coding:UTF-8
# Web dashboard to review tilt-log measurements.
#
#   streamlit run dashboard.py
#
# Pick a CSV from data/ (or upload one) and view summary stats, time series,
# the slope distribution, and the sway spectrum (FFT). Can also start a new
# measurement, which launches log_tilt.py as a separate process (the dashboard
# itself never opens the serial port).

import glob
import os
import subprocess
import sys
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


LOGGER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_tilt.py")
LOGGER_OUT = os.path.join(tempfile.gettempdir(), "verticrane_logger.out")


def logger_running():
    p = st.session_state.get("logger_proc")
    return p is not None and p.poll() is None


def start_logger(minutes):
    # Remember the existing files so we can spot the new one this run creates.
    st.session_state["logger_before"] = set(list_csvs())
    out = open(LOGGER_OUT, "w")
    st.session_state["logger_proc"] = subprocess.Popen(
        [sys.executable, LOGGER_SCRIPT, str(minutes)],
        cwd=os.path.dirname(LOGGER_SCRIPT), stdout=out, stderr=subprocess.STDOUT)
    out.close()  # the child keeps its own dup of the handle
    st.session_state["was_running"] = True


def stop_logger():
    p = st.session_state.get("logger_proc")
    if p is not None and p.poll() is None:
        p.terminate()


def newest_active_csv():
    before = st.session_state.get("logger_before", set())
    new = sorted(set(list_csvs()) - before)
    return new[-1] if new else None


def csv_rows(path):
    try:
        with open(path) as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except Exception:
        return 0


def moving_average(x, w):
    # Causal trailing average: each point is the mean of the previous w samples.
    x = np.asarray(x, dtype=float)
    if w <= 1 or len(x) < w:
        return x
    c = np.cumsum(np.insert(x, 0, 0.0))
    return (c[w:] - c[:-w]) / w


@st.cache_data
def read_csv(path, _mtime):
    # _mtime busts the cache when the file changes.
    return pd.read_csv(path)


st.set_page_config(page_title="기울기 로그 대시보드", layout="wide")
st.sidebar.title("기울기 로그")

# --- Measurement control: run the logger as a separate process ---
st.sidebar.header("측정")
minutes = st.sidebar.number_input("측정 시간 (분)", min_value=0.1, max_value=120.0,
                                  value=10.0, step=1.0)
b_start, b_stop = st.sidebar.columns(2)
if b_start.button("시작", disabled=logger_running(), use_container_width=True):
    start_logger(minutes)
    st.rerun()
if b_stop.button("정지", disabled=not logger_running(), use_container_width=True):
    stop_logger()
    st.rerun()


@st.fragment(run_every=2)
def measurement_status():
    # Rendered inside a `with st.sidebar` block, so use plain st.* here.
    if logger_running():
        active = newest_active_csv()
        st.info("측정 중... 샘플 {0}개".format(csv_rows(active) if active else 0))
    elif st.session_state.get("was_running"):
        # Just finished: full refresh so the new file appears in the list below.
        st.session_state["was_running"] = False
        st.rerun()


with st.sidebar:
    measurement_status()

with st.sidebar.expander("로거 출력"):
    try:
        with open(LOGGER_OUT) as fh:
            st.code(fh.read()[-2000:] or "(비어 있음)")
    except FileNotFoundError:
        st.caption("아직 실행된 로거가 없습니다")

# --- Choose a data source: a file from data/ or an upload ---
# While measuring, hide the in-progress file (it is still being written).
active_file = newest_active_csv() if logger_running() else None
review_files = [f for f in list_csvs() if f != active_file]
selected = st.sidebar.selectbox(
    "data/ 파일 목록", review_files, index=len(review_files) - 1,
    format_func=os.path.basename) if review_files else None
uploaded = st.sidebar.file_uploader("또는 CSV 업로드", type=["csv"])
st.sidebar.caption("임계값: {0}% (1/1000)".format(SLOPE_THRESHOLD_PCT))

path = None
if uploaded is not None:
    path = os.path.join(tempfile.gettempdir(), uploaded.name)
    with open(path, "wb") as f:
        f.write(uploaded.getbuffer())
elif selected:
    path = selected

if path is None:
    st.info("데이터가 없습니다. 측정을 실행(python log_tilt.py)하거나 CSV를 업로드하세요.")
    st.stop()

df = read_csv(path, os.path.getmtime(path))
label = os.path.basename(path)
st.title("기울기 로그 분석")
st.caption(label)

# --- Derived series ---
t = df["elapsed_s"].to_numpy()
roll = df["Roll_deg"].to_numpy()
pitch = df["Pitch_deg"].to_numpy()
slope = df["slope_pct"].to_numpy()
n = len(df)
dur = float(t[-1] - t[0]) if n > 1 else 0.0
fs = (n - 1) / dur if dur > 0 else 25.0

# --- 1-second moving average; its max is the tilt-magnitude (uprightness) metric ---
MA_SECONDS = 1.0
ma_win = max(1, int(round(MA_SECONDS * fs)))
slope_ma = moving_average(slope, ma_win)
slope_peak = float(np.nanmax(slope_ma)) if len(slope_ma) else float("nan")

# --- Summary metrics ---
cols = st.columns(6)
cols[0].metric("샘플 수", n)
cols[1].metric("샘플링 (Hz)", "{0:.1f}".format(fs))
cols[2].metric("Roll 표준편차 (deg)", "{0:.4f}".format(np.nanstd(roll)))
cols[3].metric("Pitch 표준편차 (deg)", "{0:.4f}".format(np.nanstd(pitch)))
cols[4].metric("기울기 평균 (%)", "{0:.4f}".format(np.nanmean(slope)))
cols[5].metric("기울기 1초평균 피크 (%)", "{0:.4f}".format(slope_peak))

if slope_peak > SLOPE_THRESHOLD_PCT:
    st.warning("1초 평균 기울기 피크 {0:.4f}%가 임계값 {1}%를 초과했습니다".format(
        slope_peak, SLOPE_THRESHOLD_PCT))
else:
    st.success("1초 평균 기울기 피크 {0:.4f}%가 임계값 {1}% 이내입니다".format(
        slope_peak, SLOPE_THRESHOLD_PCT))

# --- Time series (angles + slope on a secondary axis) ---
ts = make_subplots(specs=[[{"secondary_y": True}]])
ts.add_trace(go.Scatter(x=t, y=roll, name="Roll", line=dict(width=1)), secondary_y=False)
ts.add_trace(go.Scatter(x=t, y=pitch, name="Pitch", line=dict(width=1)), secondary_y=False)
ts.add_trace(go.Scatter(x=t, y=slope, name="기울기 %", line=dict(width=1, color="crimson")),
             secondary_y=True)
if len(slope_ma):
    # Align x to the actual moving-average length: the window-1 trailing offset
    # in the normal case, or the full series when the log is shorter than ma_win.
    ts.add_trace(go.Scatter(x=t[-len(slope_ma):], y=slope_ma, name="기울기 1초평균",
                            line=dict(width=2, color="darkred")), secondary_y=True)
ts.add_hline(y=SLOPE_THRESHOLD_PCT, line=dict(color="red", dash="dash"),
             secondary_y=True, annotation_text="{0}%".format(SLOPE_THRESHOLD_PCT))
ts.update_xaxes(title_text="경과 시간 (s)")
ts.update_yaxes(title_text="각도 (deg)", secondary_y=False)
ts.update_yaxes(title_text="기울기 (%)", secondary_y=True)
ts.update_layout(height=420, margin=dict(t=30, b=10), legend=dict(orientation="h"))
st.subheader("시계열")
st.plotly_chart(ts, use_container_width=True)

left, right = st.columns(2)

# --- Slope distribution ---
with left:
    st.subheader("기울기 분포")
    hist = go.Figure(go.Histogram(x=slope[~np.isnan(slope)], nbinsx=60))
    hist.update_layout(height=350, margin=dict(t=10), xaxis_title="기울기 (%)", yaxis_title="빈도")
    st.plotly_chart(hist, use_container_width=True)

# --- Sway spectrum (FFT of slope) ---
with right:
    st.subheader("흔들림 스펙트럼 (기울기 FFT)")
    s = slope[~np.isnan(slope)].astype(float)
    s = s - s.mean()
    spec = go.Figure()
    if len(s) > 1:
        window = np.hanning(len(s))
        amp = np.abs(np.fft.rfft(s * window))
        freq = np.fft.rfftfreq(len(s), d=1.0 / fs)
        spec.add_trace(go.Scatter(x=freq, y=amp, line=dict(width=1)))
        # Top-3 spectral peaks (local maxima, excluding the DC bin).
        ac = amp.copy()
        ac[0] = 0.0
        maxima = [i for i in range(1, len(ac) - 1)
                  if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1]]
        if not maxima and len(ac) > 1:
            maxima = [int(np.argmax(ac))]
        maxima.sort(key=lambda i: ac[i], reverse=True)
        top = maxima[:3]
        if top:
            spec.add_trace(go.Scatter(
                x=freq[top], y=amp[top], mode="markers+text",
                marker=dict(color="crimson", size=9),
                text=["{0:.3f} Hz".format(freq[i]) for i in top],
                textposition="top center", showlegend=False))
            labels = ["#{0} {1:.3f} Hz".format(r + 1, freq[i])
                      for r, i in enumerate(top)]
            spec.update_layout(title_text="피크: " + ", ".join(labels))
    spec.update_xaxes(title_text="주파수 (Hz)", range=[0, min(5.0, fs / 2)])
    spec.update_yaxes(title_text="진폭")
    spec.update_layout(height=350, margin=dict(t=30))
    st.plotly_chart(spec, use_container_width=True)

# --- Optional raw channels ---
with st.expander("자이로 및 온도"):
    extra = make_subplots(specs=[[{"secondary_y": True}]])
    for ax in ("GyroX_dps", "GyroY_dps", "GyroZ_dps"):
        if ax in df:
            extra.add_trace(go.Scatter(x=t, y=df[ax].to_numpy(), name=ax, line=dict(width=1)),
                            secondary_y=False)
    if "Temp_C" in df:
        extra.add_trace(go.Scatter(x=t, y=df["Temp_C"].to_numpy(), name="온도",
                                   line=dict(width=1.5, color="orange")), secondary_y=True)
    extra.update_xaxes(title_text="경과 시간 (s)")
    extra.update_yaxes(title_text="자이로 (deg/s)", secondary_y=False)
    extra.update_yaxes(title_text="온도 (C)", secondary_y=True)
    extra.update_layout(height=350, margin=dict(t=10), legend=dict(orientation="h"))
    st.plotly_chart(extra, use_container_width=True)

# --- Full text report (reuses analyze_tilt) ---
with st.expander("전체 분석 리포트"):
    # language=None disables syntax highlighting so plain-text lines (e.g. the
    # "#1 ... Hz" peak lines, otherwise dimmed as Python comments) render uniformly.
    st.code(analyze_tilt.analyze(path), language=None)
